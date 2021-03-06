/*
 * Copyright (C) 2014 Qi Chu
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Library General Public
 * License as published by the Free Software Foundation; either
 * version 2 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Library General Public License for more deroll-offss.
 *
 * You should have received a copy of the GNU Library General Public
 * License along with this library; if not, write to the
 * Free Software Foundation, Inc., 59 Temple Place - Suite 330,
 * Boston, MA 02111-1307, USA.
 */

/**
 * SECTION:element-MultirateSPIIR
 *
 * gst-launch -v
 */

/* TODO:
 *  - no update of SpiirState at run time. should support streaming format
 *  changes such as width/ rate/ quality change at run time. Should
 *  support IIR bank changes at run time.
 */
#include <cuda_debug.h>
#include <cuda_runtime.h>
#include <glib.h>
#include <gst/base/gstadapter.h>
#include <gst/base/gstbasetransform.h>
#include <gst/gst.h>
#include <gstlal/gstlal.h>
#include <math.h>
#include <multiratespiir/multiratespiir.h>
#include <multiratespiir/multiratespiir_kernel.h>
#include <multiratespiir/multiratespiir_utils.h>
#include <stdio.h>
#include <string.h>

#define GST_CAT_DEFAULT cuda_multiratespiir_debug
GST_DEBUG_CATEGORY_STATIC(GST_CAT_DEFAULT);

static void additional_initializations(GType type) {
    GST_DEBUG_CATEGORY_INIT(GST_CAT_DEFAULT, "cuda_multiratespiir", 0,
                            "cuda_multiratespiir element");
}

#define ACCELERATE_MULTIRATESPIIR_MEMORY_COPY

/*
 * add a segment to the control segment array.  note they are appended, and
 * the code assumes they are in order and do not overlap, see gstlal_gate.c
 * control_segment
 */

typedef struct flag_segment {
    GstClockTime start, stop;
    gboolean is_gap; // GAP true, non-GAP, false
} FlagSegment;

static GstFlowReturn push_with_flag(CudaMultirateSPIIR *element,
                                    GstBuffer *outbuf) {
    GstClockTime buf_start = GST_BUFFER_TIMESTAMP(outbuf), start = buf_start,
                 stop = start + GST_BUFFER_DURATION(outbuf), sub_start,
                 sub_stop;
    gboolean need_flush   = FALSE;
    GArray *flag_segments = element->flag_segments;
    guint64 buf_offset = GST_BUFFER_OFFSET(outbuf), sub_offset, sub_offset_end;
    guint outsize;
    gint out_len, pushed_len = 0;
    gint is_buf_intact = 1;
    GstFlowReturn ret;
    GstBuffer *subbuf;
    int i, flush_len = 0;
    FlagSegment *this_segment =
      &((FlagSegment *)flag_segments->data)[flag_segments->len - 1];
    /* make sure the last segment always later than the outbuf */
    g_assert(this_segment->stop >= stop);
    for (i = 0; i<flag_segments->len, stop> start; i++) {

        this_segment = &((FlagSegment *)flag_segments->data)[i];
        /*		| start				| stop
         *									| this_start
         *(1) | s | e (2)
         * | s							| e
         * | s		| e
         *            |s | e
         *            |s				| e
         */

        if (this_segment->start > stop) break;

        if (this_segment->stop < start) {
            need_flush = TRUE;
            flush_len  = i - 1;
            continue;
        }

        sub_start = this_segment->start > start ? this_segment->start : start;
        sub_stop  = this_segment->stop < stop ? this_segment->stop : stop;

        out_len        = round((double)(sub_stop - sub_start)
                        * element->offset_per_nanosecond);
        sub_offset     = element->offset0 + element->samples_out;
        sub_offset_end = sub_offset + out_len;
        outsize        = (guint)out_len * (guint)element->bps;

        GST_DEBUG_OBJECT(
          element,
          "segment len %d, processing %d, start %" GST_TIME_FORMAT
          ", stop %" GST_TIME_FORMAT " segment start %" GST_TIME_FORMAT
          ", segment stop %" GST_TIME_FORMAT " out_len %d, out_size %u",
          flag_segments->len, i, GST_TIME_ARGS(start), GST_TIME_ARGS(stop),
          GST_TIME_ARGS(this_segment->start), GST_TIME_ARGS(this_segment->stop),
          out_len, outsize);
        if (out_len > 0) {
            /* note that the buf->data is gunit8 *, so need to calculate the
             * offset for subbuf */
            subbuf = gst_buffer_create_sub(
              outbuf,
              (guint)(sub_offset - GST_BUFFER_OFFSET(outbuf)) * element->bps,
              outsize);
            if (!subbuf) {
                GST_ERROR_OBJECT(element, "failing creating sub-buffer");
                return GST_FLOW_ERROR;
            }

            subbuf = gst_buffer_make_writable(subbuf);
            gst_buffer_copy_metadata(
              subbuf, outbuf, GST_BUFFER_COPY_FLAGS | GST_BUFFER_COPY_CAPS);
            GST_BUFFER_TIMESTAMP(subbuf)  = sub_start;
            GST_BUFFER_DURATION(subbuf)   = sub_stop - sub_start;
            GST_BUFFER_OFFSET(subbuf)     = sub_offset;
            GST_BUFFER_OFFSET_END(subbuf) = sub_offset_end;
            GST_BUFFER_SIZE(subbuf)       = outsize;

            if (this_segment->is_gap) {
                GST_BUFFER_FLAG_SET(subbuf, GST_BUFFER_FLAG_GAP);
            }

            GST_LOG_OBJECT(
              element,
              "Created sub buffer of (%u bytes) with timestamp "
              "%" GST_TIME_FORMAT ", duration %" GST_TIME_FORMAT
              ", offset %" G_GUINT64_FORMAT ", offset_end %" G_GUINT64_FORMAT
              " with flag %d from buf with timestamp %" GST_TIME_FORMAT
              " duration %" GST_TIME_FORMAT "first value %f, second value %f",
              outsize, GST_TIME_ARGS(sub_start),
              GST_TIME_ARGS(sub_stop - sub_start), sub_offset, sub_offset_end,
              this_segment->is_gap, GST_TIME_ARGS(GST_BUFFER_TIMESTAMP(outbuf)),
              GST_TIME_ARGS(GST_BUFFER_DURATION(outbuf)),
              (float *)GST_BUFFER_DATA(subbuf)[0],
              (float *)GST_BUFFER_DATA(subbuf)[1]);

            ret = gst_pad_push(element->srcpad, subbuf);
            GST_LOG_OBJECT(element, "pushed sub buffer, result = %s",
                           gst_flow_get_name(ret));
            is_buf_intact = 0;
            element->samples_out += out_len;
            start = sub_stop;
            pushed_len += out_len;
        }
    }
    g_assert(pushed_len
             == GST_BUFFER_OFFSET_END(outbuf) - GST_BUFFER_OFFSET(outbuf));

    if (need_flush && flush_len > 0)
        g_array_remove_range(flag_segments, 0, flush_len);

    if (is_buf_intact) {
        gst_buffer_ref(outbuf); /* need the transform to free it */
        ret = gst_pad_push(element->srcpad, outbuf);
        GST_LOG_OBJECT(element, "pushed original buffer, result = %s",
                       gst_flow_get_name(ret));
    }
    return GST_FLOW_OK;
}

static void add_flag_segment(CudaMultirateSPIIR *element,
                             GstClockTime start,
                             GstClockTime stop,
                             gboolean is_gap) {
    FlagSegment new_segment = { .start  = start,
                                .stop   = stop,
                                .is_gap = is_gap };

    g_assert_cmpuint(start, <=, stop);
    GST_DEBUG_OBJECT(element,
                     "found flag segment [%" GST_TIME_FORMAT
                     ", %" GST_TIME_FORMAT ") in state %d",
                     GST_TIME_ARGS(new_segment.start),
                     GST_TIME_ARGS(new_segment.stop), new_segment.is_gap);

    /* try coalescing the new segment with the most recent one */
    if (element->flag_segments->len) {
        FlagSegment *final_segment =
          &((FlagSegment *)
              element->flag_segments->data)[element->flag_segments->len - 1];
        /* if the most recent segment and the new segment have the
         * same state and they touch, merge them */
        if (final_segment->is_gap == new_segment.is_gap
            && final_segment->stop >= new_segment.start) {
            g_assert_cmpuint(new_segment.stop, >=, final_segment->stop);
            final_segment->stop = new_segment.stop;
            return;
        }
        /* otherwise, if the most recent segment had 0 length,
         * replace it entirely with the new one.  note that the
         * state carried by a zero-length segment is meaningless,
         * zero-length segments are merely interpreted as a
         * heart-beat indicating how far the flag stream has
         * advanced */
        if (final_segment->stop == final_segment->start) {
            *final_segment = new_segment;
            return;
        }
    }
    /* otherwise append a new segment */
    g_array_append_val(element->flag_segments, new_segment);
}

GST_BOILERPLATE_FULL(CudaMultirateSPIIR,
                     cuda_multiratespiir,
                     GstBaseTransform,
                     GST_TYPE_BASE_TRANSFORM,
                     additional_initializations);

enum { PROP_0, PROP_IIRBANK_FNAME, PROP_GAP_HANDLE, PROP_STREAM_ID };

// FIXME: not support width=64 yet
static GstStaticPadTemplate cuda_multiratespiir_sink_template =
  GST_STATIC_PAD_TEMPLATE("sink",
                          GST_PAD_SINK,
                          GST_PAD_ALWAYS,
                          GST_STATIC_CAPS("audio/x-raw-float, "
                                          "rate = (int) [1, MAX], "
                                          "channels = (int) 1, "
                                          "endianness = (int) BYTE_ORDER, "
                                          "width = (int) 32"));

static GstStaticPadTemplate cuda_multiratespiir_src_template =
  GST_STATIC_PAD_TEMPLATE("src",
                          GST_PAD_SRC,
                          GST_PAD_ALWAYS,
                          GST_STATIC_CAPS("audio/x-raw-float, "
                                          "rate = (int) [1, MAX], "
                                          "channels = (int) [1, MAX], "
                                          "endianness = (int) BYTE_ORDER, "
                                          "width = (int) 32"));

static void cuda_multiratespiir_set_property(GObject *object,
                                             guint prop_id,
                                             const GValue *value,
                                             GParamSpec *pspec);
static void cuda_multiratespiir_get_property(GObject *object,
                                             guint prop_id,
                                             GValue *value,
                                             GParamSpec *pspec);

/* vmethods */
static gboolean cuda_multiratespiir_get_unit_size(GstBaseTransform *base,
                                                  GstCaps *caps,
                                                  guint *size);
static GstCaps *cuda_multiratespiir_transform_caps(GstBaseTransform *base,
                                                   GstPadDirection direction,
                                                   GstCaps *caps);
static gboolean cuda_multiratespiir_set_caps(GstBaseTransform *base,
                                             GstCaps *incaps,
                                             GstCaps *outcaps);
static GstFlowReturn cuda_multiratespiir_transform(GstBaseTransform *base,
                                                   GstBuffer *inbuf,
                                                   GstBuffer *outbuf);
static gboolean cuda_multiratespiir_transform_size(GstBaseTransform *base,
                                                   GstPadDirection direction,
                                                   GstCaps *caps,
                                                   guint size,
                                                   GstCaps *othercaps,
                                                   guint *othersize);
static gboolean cuda_multiratespiir_event(GstBaseTransform *base,
                                          GstEvent *event);
static gboolean cuda_multiratespiir_start(GstBaseTransform *base);
static gboolean cuda_multiratespiir_stop(GstBaseTransform *base);
// FIXME: query
// static gboolean cuda_multiratespiir_query (GstPad * pad, GstQuery * query);
// static const GstQueryType *cuda_multiratespiir_query_type (GstPad * pad);

static void cuda_multiratespiir_base_init(gpointer g_class) {
    GstElementClass *gstelement_class = GST_ELEMENT_CLASS(g_class);

    gst_element_class_set_details_simple(
      gstelement_class, "Multirate SPIIR",
      "multi level downsample + spiir + upsample",
      "single rate data stream -> multi template SNR streams",
      "Qi Chu <qi.chu@ligo.org>");

    gst_element_class_add_pad_template(
      gstelement_class,
      gst_static_pad_template_get(&cuda_multiratespiir_src_template));
    gst_element_class_add_pad_template(
      gstelement_class,
      gst_static_pad_template_get(&cuda_multiratespiir_sink_template));

    GST_BASE_TRANSFORM_CLASS(g_class)->start =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_start);
    GST_BASE_TRANSFORM_CLASS(g_class)->stop =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_stop);
    GST_BASE_TRANSFORM_CLASS(g_class)->get_unit_size =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_get_unit_size);
    GST_BASE_TRANSFORM_CLASS(g_class)->transform_caps =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_transform_caps);
    GST_BASE_TRANSFORM_CLASS(g_class)->set_caps =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_set_caps);
    GST_BASE_TRANSFORM_CLASS(g_class)->transform =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_transform);
    GST_BASE_TRANSFORM_CLASS(g_class)->transform_size =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_transform_size);
    GST_BASE_TRANSFORM_CLASS(g_class)->event =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_event);
}

static void cuda_multiratespiir_class_init(CudaMultirateSPIIRClass *klass) {
    GObjectClass *gobject_class = (GObjectClass *)klass;

    gobject_class->set_property =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_set_property);
    gobject_class->get_property =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_get_property);

    g_object_class_install_property(
      gobject_class, PROP_IIRBANK_FNAME,
      g_param_spec_string(
        "bank-fname", "The file of IIR bank feedback coefficients",
        "A parallel bank of first order IIR filter feedback coefficients.",
        NULL, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_GAP_HANDLE,
      g_param_spec_int("gap-handle", "gap handling",
                       "restart after gap (1), or gap is treated as 0 (0)", 0,
                       1, 0, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_STREAM_ID,
      g_param_spec_int("stream-id", "id for cuda stream", "id for cuda stream",
                       0, G_MAXINT, 0,
                       G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
}

static void cuda_multiratespiir_init(CudaMultirateSPIIR *element,
                                     CudaMultirateSPIIRClass *klass) {
    //  GstBaseTransform *trans = GST_BASE_TRANSFORM (element);
    element->iir_bank_lock       = g_mutex_new();
    element->iir_bank_available  = g_cond_new();
    element->bank_fname          = NULL;
    element->num_depths          = 0;
    element->outchannels         = 0;
    element->spstate             = NULL;
    element->spstate_initialised = FALSE;
    element->num_exe_samples     = 4096; // assumes the rate=4096Hz
    element->num_head_cover_samples =
      13120; // assumes the rate=4096Hz, down quality = 9
    element->num_tail_cover_samples = 13104; // assumes the rate=4096Hz

    //  gst_base_transform_set_gap_aware (trans, TRUE);
    //  gst_pad_set_query_function (trans->srcpad, cuda_multiratespiir_query);
    // gst_pad_set_query_type_function (trans->srcpad,
    //      cuda_multiratespiir_query_type);

    // for ACCELERATE_MULTIRATESPIIR_MEMORY_COPY
    element->h_snglsnr_buffer   = NULL;
    element->len_snglsnr_buffer = 0;
    element->srcpad             = gst_element_get_static_pad(element, "src");
}

/* vmethods */
static gboolean cuda_multiratespiir_start(GstBaseTransform *base) {
    CudaMultirateSPIIR *element = CUDA_MULTIRATESPIIR(base);

    element->adapter       = gst_adapter_new();
    element->flag_segments = g_array_new(FALSE, FALSE, sizeof(FlagSegment));

    element->need_discont    = TRUE;
    element->num_gap_samples = 0;
    element->need_tail_drain = FALSE;
    element->t0              = GST_CLOCK_TIME_NONE;
    element->offset0         = GST_BUFFER_OFFSET_NONE;
    element->next_in_offset  = GST_BUFFER_OFFSET_NONE;
    element->samples_in      = 0;
    element->samples_out     = 0;
    return TRUE;
}

static gboolean cuda_multiratespiir_stop(GstBaseTransform *base) {
    CudaMultirateSPIIR *element = CUDA_MULTIRATESPIIR(base);

    g_mutex_free(element->iir_bank_lock);
    g_cond_free(element->iir_bank_available);

    if (element->spstate) {
        spiir_state_destroy(element->spstate, element->num_depths);
    }

    g_object_unref(element->adapter);
    element->adapter = NULL;
    g_array_unref(element->flag_segments);
    element->flag_segments = NULL;

    return TRUE;
}

static gboolean cuda_multiratespiir_get_unit_size(GstBaseTransform *base,
                                                  GstCaps *caps,
                                                  guint *size) {
    gint width, channels;
    GstStructure *structure;
    gboolean ret;

    g_return_val_if_fail(size != NULL, FALSE);

    /* this works for both float and int */
    structure = gst_caps_get_structure(caps, 0);
    ret       = gst_structure_get_int(structure, "width", &width);
    ret &= gst_structure_get_int(structure, "channels", &channels);

    if (G_UNLIKELY(!ret)) return FALSE;

    *size = (width / 8) * channels;
    GST_DEBUG_OBJECT(base, "get unit size of caps %d", *size);

    return TRUE;
}

static GstCaps *cuda_multiratespiir_transform_caps(GstBaseTransform *base,
                                                   GstPadDirection direction,
                                                   GstCaps *caps) {
    CudaMultirateSPIIR *element = CUDA_MULTIRATESPIIR(base);

    GstCaps *othercaps;

    othercaps = gst_caps_copy(caps);

    switch (direction) {
    case GST_PAD_SRC:
        /*
         * sink caps is the same with src caps, except it only has 1 channel
         */

        gst_structure_set(gst_caps_get_structure(othercaps, 0), "channels",
                          G_TYPE_INT, 1, NULL);
        GST_LOG("setting channels to 1\n");
        break;

    case GST_PAD_SINK:
        /*
         * src caps is the same with sink caps, except it only has number of
         * channels that equals to the number of templates
         */
        // if (!g_mutex_trylock(element->iir_bank_lock))
        // printf("lock by another thread");
        g_mutex_lock(element->iir_bank_lock);
        if (!element->spstate)
            g_cond_wait(element->iir_bank_available, element->iir_bank_lock);

        gst_structure_set(gst_caps_get_structure(othercaps, 0), "channels",
                          G_TYPE_INT,
                          cuda_multiratespiir_get_outchannels(element), NULL);
        g_mutex_unlock(element->iir_bank_lock);
        break;

    case GST_PAD_UNKNOWN:
        GST_ELEMENT_ERROR(base, CORE, NEGOTIATION, (NULL),
                          ("invalid direction GST_PAD_UNKNOWN"));
        gst_caps_unref(othercaps);
        return GST_CAPS_NONE;
    }

    return othercaps;
}

// Note: sizes calculated here are uplimit sizes, not necessarily the true
// sizes.

static gboolean cuda_multiratespiir_transform_size(GstBaseTransform *base,
                                                   GstPadDirection direction,
                                                   GstCaps *caps,
                                                   guint size,
                                                   GstCaps *othercaps,
                                                   guint *othersize) {
    CudaMultirateSPIIR *element = CUDA_MULTIRATESPIIR(base);
    gboolean ret                = TRUE;

    guint unit_size, other_unit_size;
    GST_LOG_OBJECT(base, "asked to transform size %d in direction %s", size,
                   direction == GST_PAD_SINK ? "SINK" : "SRC");

    if (!cuda_multiratespiir_get_unit_size(base, caps, &unit_size))
        return FALSE;

    if (!cuda_multiratespiir_get_unit_size(base, othercaps, &other_unit_size))
        return FALSE;

    if (direction == GST_PAD_SINK) {
        /*
         * asked to convert size of an incoming buffer. The output size
         * is the uplimit size.
         */
        //    g_assert(element->bank_initialised == TRUE);
        GST_LOG_OBJECT(base, "available samples  %d",
                       cuda_multiratespiir_get_available_samples(element));
        *othersize = (size / unit_size
                      + cuda_multiratespiir_get_available_samples(element))
                     * other_unit_size;
    } else {
        /* asked to convert size of an outgoing buffer.
         */
        //    g_assert(element->bank_initialised == TRUE);
        *othersize = (size / unit_size) * other_unit_size;
    }

    GST_LOG_OBJECT(base, "transformed size %d to %d", size, *othersize);

    return ret;
}

static gboolean cuda_multiratespiir_set_caps(GstBaseTransform *base,
                                             GstCaps *incaps,
                                             GstCaps *outcaps) {
    CudaMultirateSPIIR *element = CUDA_MULTIRATESPIIR(base);
    GstStructure *s;
    gint rate;
    gint channels;
    gint width;
    gboolean success = TRUE;

    GST_LOG_OBJECT(element,
                   "incaps %" GST_PTR_FORMAT ", outcaps %" GST_PTR_FORMAT,
                   incaps, outcaps);

    s = gst_caps_get_structure(outcaps, 0);
    success &= gst_structure_get_int(s, "channels", &channels);
    success &= gst_structure_get_int(s, "width", &width);
    success &= gst_structure_get_int(s, "rate", &rate);

    g_mutex_lock(element->iir_bank_lock);
    if (!element->spstate)
        g_cond_wait(element->iir_bank_available, element->iir_bank_lock);

    if (!success) {
        GST_ERROR_OBJECT(element,
                         "unable to parse and/or accept caps %" GST_PTR_FORMAT,
                         outcaps);
    }

    if (channels != (gint)cuda_multiratespiir_get_outchannels(element)) {
        /* impossible to happen */
        GST_ERROR_OBJECT(element, "channels != %d in %" GST_PTR_FORMAT,
                         cuda_multiratespiir_get_outchannels(element), outcaps);
        success = FALSE;
    }

    if (width != (gint)element->width) {
        /*
         * FIXME :do not support width change at run time
         */
        GST_ERROR_OBJECT(element, "width != %d in %" GST_PTR_FORMAT,
                         element->width, outcaps);
        success = FALSE;
    }

    if (rate != (gint)element->rate) {
        /*
         * FIXME: do not support rate change at run time
         */
        GST_ERROR_OBJECT(element, "rate != %d in %" GST_PTR_FORMAT,
                         element->rate, outcaps);
        success = FALSE;
    }
    element->bps                   = width / 8 * channels; // bytes per sample
    element->offset_per_nanosecond = element->rate / 1e9;
    /* transform_caps already done, num_depths already set */

    g_mutex_unlock(element->iir_bank_lock);
    return success;
}

/* c downsample2x */
#if 0
static void
downsample2x(ResamplerState *state, float *in, const gint num_inchunk, float *out, gint *out_processed)
{
  float *pos_mem;
  pos_mem = state->mem;
  gint filt_offs = state->filt_len - 1;
  gint j;
  for (j = 0; j < num_inchunk; ++j)
    pos_mem[j + filt_offs] = in[j];

  /*
   * FIXME: not filter yet
   */
  *out_processed = num_inchunk/2;
  for (j = 0; j < *out_processed; ++j)
    out[j] = in[j];
}
#endif

static GstFlowReturn cuda_multiratespiir_assemble_gap_buffer(
  CudaMultirateSPIIR *element, gint len, GstBuffer *gapbuf) {
    gint outsize            = len * element->outchannels * element->width / 8;
    GST_BUFFER_SIZE(gapbuf) = outsize;

    /* time */
    if (GST_CLOCK_TIME_IS_VALID(element->t0)) {
        GST_BUFFER_TIMESTAMP(gapbuf) =
          element->t0
          + gst_util_uint64_scale_int_round(element->samples_out, GST_SECOND,
                                            element->rate);
        GST_BUFFER_DURATION(gapbuf) =
          element->t0
          + gst_util_uint64_scale_int_round(element->samples_out + len,
                                            GST_SECOND, element->rate)
          - GST_BUFFER_TIMESTAMP(gapbuf);
    } else {
        GST_BUFFER_TIMESTAMP(gapbuf) = GST_CLOCK_TIME_NONE;
        GST_BUFFER_DURATION(gapbuf)  = GST_CLOCK_TIME_NONE;
    }
    /* offset */
    if (element->offset0 != GST_BUFFER_OFFSET_NONE) {
        GST_BUFFER_OFFSET(gapbuf)     = element->offset0 + element->samples_out;
        GST_BUFFER_OFFSET_END(gapbuf) = GST_BUFFER_OFFSET(gapbuf) + len;
    } else {
        GST_BUFFER_OFFSET(gapbuf)     = GST_BUFFER_OFFSET_NONE;
        GST_BUFFER_OFFSET_END(gapbuf) = GST_BUFFER_OFFSET_NONE;
    }

    if (element->need_discont) {
        GST_BUFFER_FLAG_SET(gapbuf, GST_BUFFER_FLAG_DISCONT);
        element->need_discont = FALSE;
    }

    GST_BUFFER_FLAG_SET(gapbuf, GST_BUFFER_FLAG_GAP);

    /* move along */
    element->samples_out += len;
    element->samples_in += len;

    GST_LOG_OBJECT(
      element,
      "Assembled gap buffer of %u bytes with timestamp %" GST_TIME_FORMAT
      " duration %" GST_TIME_FORMAT " offset %" G_GUINT64_FORMAT
      " offset_end %" G_GUINT64_FORMAT,
      GST_BUFFER_SIZE(gapbuf), GST_TIME_ARGS(GST_BUFFER_TIMESTAMP(gapbuf)),
      GST_TIME_ARGS(GST_BUFFER_DURATION(gapbuf)), GST_BUFFER_OFFSET(gapbuf),
      GST_BUFFER_OFFSET_END(gapbuf));

    if (outsize == 0) {
        GST_DEBUG_OBJECT(element, "buffer dropped");
        return GST_BASE_TRANSFORM_FLOW_DROPPED;
    }

    return GST_FLOW_OK;
}

static GstFlowReturn cuda_multiratespiir_push_gap(CudaMultirateSPIIR *element,
                                                  gint gap_len) {
    GstBuffer *gapbuf;
    guint outsize     = gap_len * sizeof(float) * element->outchannels;
    GstFlowReturn res = gst_pad_alloc_buffer_and_set_caps(
      GST_BASE_TRANSFORM_SRC_PAD(element), GST_BUFFER_OFFSET_NONE, outsize,
      GST_PAD_CAPS(GST_BASE_TRANSFORM_SRC_PAD(element)), &gapbuf);

    // FIXME: no sanity check
    res = cuda_multiratespiir_assemble_gap_buffer(element, gap_len, gapbuf);

    res = gst_pad_push(GST_BASE_TRANSFORM_SRC_PAD(element), gapbuf);

    if (G_UNLIKELY(res != GST_FLOW_OK))
        GST_WARNING_OBJECT(element, "Failed to push gap: %s",
                           gst_flow_get_name(res));
    return res;
}

static GstFlowReturn cuda_multiratespiir_push_drain(CudaMultirateSPIIR *element,
                                                    gint in_len) {
    gint num_in_multidown, num_out_multidown,
      num_out_spiirup = 0, last_num_out_spiirup = 0, old_in_len = in_len;

    num_in_multidown = MIN(old_in_len, element->num_exe_samples);

    gint outsize = 0, out_len = 0, upfilt_len;
    float *in_multidown, *pos_out;
    upfilt_len = element->spstate[0]->upstate->filt_len;
#if 0
  gint tmp_out_len = 0;
  float *tmp_out;
  tmp_out_len = element->spstate[0]->upstate->mem_len;
  tmp_out = (float *)malloc(element->outchannels * tmp_out_len * sizeof(float));
#endif

    gint i, j;
    GstBuffer *outbuf;
    GstFlowReturn res;
    float *outdata;

    /* To restore the buffer timestamp, out length must be equal to in length */
    // out_len = spiir_state_get_outlen (element->spstate, in_len,
    // element->num_depths);
    if (element->num_exe_samples == element->rate) out_len = in_len;
    else
        out_len = in_len - element->num_tail_cover_samples;

    outsize = out_len * sizeof(float) * element->outchannels;

    res = gst_pad_alloc_buffer_and_set_caps(
      GST_BASE_TRANSFORM_SRC_PAD(element), GST_BUFFER_OFFSET_NONE, outsize,
      GST_PAD_CAPS(GST_BASE_TRANSFORM_SRC_PAD(element)), &outbuf);

    memset(GST_BUFFER_DATA(outbuf), 0, outsize);

    if (G_UNLIKELY(res != GST_FLOW_OK)) {
        GST_WARNING_OBJECT(element, "failed allocating buffer of %d bytes",
                           outsize);
        return res;
    }

    outdata = (float *)GST_BUFFER_DATA(outbuf);
    while (num_in_multidown > 0) {

        g_assert(gst_adapter_available(element->adapter)
                 >= num_in_multidown * sizeof(float));
        in_multidown = (float *)gst_adapter_peek(
          element->adapter, num_in_multidown * sizeof(float));

        num_out_multidown = multi_downsample(
          element->spstate, in_multidown, (gint)num_in_multidown,
          element->num_depths, element->stream);
        pos_out = outdata + last_num_out_spiirup * (element->outchannels);
        num_out_spiirup =
          spiirup(element->spstate, num_out_multidown, element->num_depths,
                  pos_out, element->stream);

#if 0
    /* reshape is deprecated because it cost hugh cpu usage */
    /* reshape to the outbuf data */
    for (i=0; i<num_out_spiirup; i++)
      for (j=0; j<element->outchannels; j++)
	      outdata[element->outchannels * (i + last_num_out_spiirup) + j] = tmp_out[tmp_out_len * j + i + upfilt_len - 1];

    //memcpy(pos_out, tmp_out, sizeof(float) * num_out_spiirup * (element->outchannels));
    //free(tmp_out);
#endif

        GST_DEBUG_OBJECT(element, "done cpy data to BUFFER");

        /* move along */
        gst_adapter_flush(element->adapter, num_in_multidown * sizeof(float));
        in_len -= num_in_multidown;
        /* after the first filtering, update the exe_samples to the rate */
        cuda_multiratespiir_update_exe_samples(&element->num_exe_samples,
                                               element->rate);
        num_in_multidown = MIN(in_len, element->num_exe_samples);
        last_num_out_spiirup += num_out_spiirup;
    }

    g_assert(last_num_out_spiirup <= out_len);

    /* time */
    if (GST_CLOCK_TIME_IS_VALID(element->t0)) {
        GST_BUFFER_TIMESTAMP(outbuf) =
          element->t0
          + gst_util_uint64_scale_int_round(element->samples_out, GST_SECOND,
                                            element->rate);
        GST_BUFFER_DURATION(outbuf) =
          element->t0
          + gst_util_uint64_scale_int_round(element->samples_out + out_len,
                                            GST_SECOND, element->rate)
          - GST_BUFFER_TIMESTAMP(outbuf);
    } else {
        GST_BUFFER_TIMESTAMP(outbuf) = GST_CLOCK_TIME_NONE;
        GST_BUFFER_DURATION(outbuf)  = GST_CLOCK_TIME_NONE;
    }
    /* offset */
    if (element->offset0 != GST_BUFFER_OFFSET_NONE) {
        GST_BUFFER_OFFSET(outbuf)     = element->offset0 + element->samples_out;
        GST_BUFFER_OFFSET_END(outbuf) = GST_BUFFER_OFFSET(outbuf) + out_len;
    } else {
        GST_BUFFER_OFFSET(outbuf)     = GST_BUFFER_OFFSET_NONE;
        GST_BUFFER_OFFSET_END(outbuf) = GST_BUFFER_OFFSET_NONE;
    }

    if (element->need_discont) {
        GST_BUFFER_FLAG_SET(outbuf, GST_BUFFER_FLAG_DISCONT);
        element->need_discont = FALSE;
    }

    element->samples_out += out_len;
    element->samples_in += old_in_len;

    GST_BUFFER_SIZE(outbuf) = outsize;

    GST_LOG_OBJECT(element,
                   "Push_drain: Converted to buffer of %" G_GUINT32_FORMAT
                   " samples (%u bytes) with timestamp %" GST_TIME_FORMAT
                   ", duration %" GST_TIME_FORMAT ", offset %" G_GUINT64_FORMAT
                   ", offset_end %" G_GUINT64_FORMAT,
                   out_len, GST_BUFFER_SIZE(outbuf),
                   GST_TIME_ARGS(GST_BUFFER_TIMESTAMP(outbuf)),
                   GST_TIME_ARGS(GST_BUFFER_DURATION(outbuf)),
                   GST_BUFFER_OFFSET(outbuf), GST_BUFFER_OFFSET_END(outbuf));

    if (outsize == 0) {
        GST_DEBUG_OBJECT(element, "buffer dropped");
        gst_object_unref(outbuf);
        return GST_BASE_TRANSFORM_FLOW_DROPPED;
    }

    res = gst_pad_push(GST_BASE_TRANSFORM_SRC_PAD(element), outbuf);

    if (G_UNLIKELY(res != GST_FLOW_OK))
        GST_WARNING_OBJECT(element, "Failed to push drain: %s",
                           gst_flow_get_name(res));
    return res;

    return GST_FLOW_OK;
}

static GstFlowReturn cuda_multiratespiir_process(CudaMultirateSPIIR *element,
                                                 gint in_len,
                                                 GstBuffer *outbuf) {
    gint num_exe_samples, num_in_multidown, num_out_multidown, num_out_spiirup,
      last_num_out_spiirup = 0, old_in_len = in_len;

    num_exe_samples  = element->num_exe_samples;
    num_in_multidown = MIN(old_in_len, num_exe_samples);

    gint outsize = 0, out_len = 0, upfilt_len;
    float *in_multidown;
    upfilt_len = element->spstate[0]->upstate->filt_len;
    // int tmp_out_len = element->spstate[0]->upstate->mem_len;
    // float *tmp_out = (float *)malloc(element->outchannels * tmp_out_len *
    // sizeof(float));

    gint i, j;
    float *outdata, *pos_out;

    if (element->num_exe_samples == element->rate) out_len = in_len;
    else
        out_len = in_len - element->num_tail_cover_samples;

    outsize = out_len * element->bps;

    // GST_DEBUG_OBJECT (element, "tmp_out_len %d, out len predicted %d",
    // tmp_out_len, out_len);

#ifdef ACCELERATE_MULTIRATESPIIR_MEMORY_COPY
    // to accelerate gpu memory copy, first gpu->cpu(pinned
    // memory)->cpu(gstbuffer) remember copy from h_snglsnr_buffer to gstbuffer
    // should update this part of code after porting to 1.0
    g_assert(element->len_snglsnr_buffer > 0
             || (element->len_snglsnr_buffer == 0
                 && element->h_snglsnr_buffer == NULL));
    if (outsize > element->len_snglsnr_buffer) {
        if (element->h_snglsnr_buffer != NULL) {
            cudaFreeHost(element->h_snglsnr_buffer);
        }
        cudaMallocHost((void **)&element->h_snglsnr_buffer, outsize);
        element->len_snglsnr_buffer = outsize;
    }
    outdata = (float *)element->h_snglsnr_buffer;
#else
    outdata = (float *)GST_BUFFER_DATA(outbuf);
#endif

    while (num_in_multidown > 0) {

        g_assert(gst_adapter_available(element->adapter)
                 >= num_in_multidown * sizeof(float));
        in_multidown = (float *)gst_adapter_peek(
          element->adapter, num_in_multidown * sizeof(float));

        num_out_multidown = multi_downsample(
          element->spstate, in_multidown, (gint)num_in_multidown,
          element->num_depths, element->stream);
        pos_out = outdata + last_num_out_spiirup * (element->outchannels);
        num_out_spiirup =
          spiirup(element->spstate, num_out_multidown, element->num_depths,
                  pos_out, element->stream);
        // num_out_spiirup = spiirup (element->spstate, num_out_multidown,
        // element->num_depths, tmp_out, element->stream);

#if 0
    /* reshape is deprecated because it cost hugh cpu usage */
    /* reshape to the outbuf data */
    for (i=0; i<num_out_spiirup; i++)
      for (j=0; j<element->outchannels; j++)
	      outdata[element->outchannels * (i + last_num_out_spiirup) + j] = tmp_out[tmp_out_len * j + i + upfilt_len - 1];

    //memcpy(pos_out, tmp_out, sizeof(float) * num_out_spiirup * (element->outchannels));
    //free(tmp_out);
#endif

        GST_DEBUG_OBJECT(element, "done cpy data to BUFFER");

        /* move along */
        gst_adapter_flush(element->adapter, num_in_multidown * sizeof(float));
        in_len -= num_in_multidown;
        num_in_multidown = MIN(in_len, num_exe_samples);
        last_num_out_spiirup += num_out_spiirup;
    }

    g_assert(last_num_out_spiirup == out_len);

#ifdef ACCELERATE_MULTIRATESPIIR_MEMORY_COPY
    memcpy((void *)GST_BUFFER_DATA(outbuf), outdata, outsize);
#endif

    /* time */
    if (GST_CLOCK_TIME_IS_VALID(element->t0)) {
        GST_BUFFER_TIMESTAMP(outbuf) =
          element->t0
          + gst_util_uint64_scale_int_round(element->samples_out, GST_SECOND,
                                            element->rate);
        GST_BUFFER_DURATION(outbuf) =
          element->t0
          + gst_util_uint64_scale_int_round(element->samples_out + out_len,
                                            GST_SECOND, element->rate)
          - GST_BUFFER_TIMESTAMP(outbuf);
    } else {
        GST_BUFFER_TIMESTAMP(outbuf) = GST_CLOCK_TIME_NONE;
        GST_BUFFER_DURATION(outbuf)  = GST_CLOCK_TIME_NONE;
    }
    /* offset */
    if (element->offset0 != GST_BUFFER_OFFSET_NONE) {
        GST_BUFFER_OFFSET(outbuf)     = element->offset0 + element->samples_out;
        GST_BUFFER_OFFSET_END(outbuf) = GST_BUFFER_OFFSET(outbuf) + out_len;
    } else {
        GST_BUFFER_OFFSET(outbuf)     = GST_BUFFER_OFFSET_NONE;
        GST_BUFFER_OFFSET_END(outbuf) = GST_BUFFER_OFFSET_NONE;
    }

    if (element->need_discont) {
        GST_BUFFER_FLAG_SET(outbuf, GST_BUFFER_FLAG_DISCONT);
        element->need_discont = FALSE;
    }

    element->samples_in += old_in_len;

    GST_BUFFER_SIZE(outbuf) = outsize;

    GST_LOG_OBJECT(element,
                   "Converted to buffer of %" G_GUINT32_FORMAT
                   " samples (%u bytes) with timestamp %" GST_TIME_FORMAT
                   ", duration %" GST_TIME_FORMAT ", offset %" G_GUINT64_FORMAT
                   ", offset_end %" G_GUINT64_FORMAT,
                   out_len, GST_BUFFER_SIZE(outbuf),
                   GST_TIME_ARGS(GST_BUFFER_TIMESTAMP(outbuf)),
                   GST_TIME_ARGS(GST_BUFFER_DURATION(outbuf)),
                   GST_BUFFER_OFFSET(outbuf), GST_BUFFER_OFFSET_END(outbuf));

    if (outsize == 0) {
        GST_DEBUG_OBJECT(element, "buffer dropped");
        return GST_BASE_TRANSFORM_FLOW_DROPPED;
    }

    /* after the first filtering, update the exe_samples to the rate */
    cuda_multiratespiir_update_exe_samples(&element->num_exe_samples,
                                           element->rate);

    GstFlowReturn ret = push_with_flag(element, outbuf);
    if (ret != GST_FLOW_OK) return ret;
    else
        return GST_BASE_TRANSFORM_FLOW_DROPPED;
}

/*
 * construct a buffer of zeros and push into adapter
 */

static void adapter_push_zeros(CudaMultirateSPIIR *element, unsigned samples) {
    GstBuffer *zerobuf =
      gst_buffer_new_and_alloc(samples * (element->width / 8));
    if (!zerobuf) {
        GST_DEBUG_OBJECT(element, "failure allocating zero-pad buffer");
    }
    memset(GST_BUFFER_DATA(zerobuf), 0, GST_BUFFER_SIZE(zerobuf));
    gst_adapter_push(element->adapter, zerobuf);
}

static GstFlowReturn cuda_multiratespiir_transform(GstBaseTransform *base,
                                                   GstBuffer *inbuf,
                                                   GstBuffer *outbuf) {
    /*
     * output buffer is generated in cuda_multiratespiir_process function.
     */

    CudaMultirateSPIIR *element = CUDA_MULTIRATESPIIR(base);
    GstFlowReturn res;

    gulong size;
    size = GST_BUFFER_SIZE(inbuf);

    GST_LOG_OBJECT(
      element,
      "transforming %s+%s buffer of %ld bytes, ts %" GST_TIME_FORMAT
      ", duration %" GST_TIME_FORMAT ", offset %" G_GINT64_FORMAT
      ", offset_end %" G_GINT64_FORMAT,
      GST_BUFFER_FLAG_IS_SET(inbuf, GST_BUFFER_FLAG_GAP) ? "GAP" : "NONGAP",
      GST_BUFFER_IS_DISCONT(inbuf) ? "DISCONT" : "CONT", size,
      GST_TIME_ARGS(GST_BUFFER_TIMESTAMP(inbuf)),
      GST_TIME_ARGS(GST_BUFFER_DURATION(inbuf)), GST_BUFFER_OFFSET(inbuf),
      GST_BUFFER_OFFSET_END(inbuf));

    /*
     * set device context
     */

    g_mutex_lock(element->iir_bank_lock);
    if (!element->spstate_initialised) {
        g_cond_wait(element->iir_bank_available, element->iir_bank_lock);
    }
    g_mutex_unlock(element->iir_bank_lock);

    CUDA_CHECK(cudaSetDevice(element->deviceID));
    /* check for timestamp discontinuities;  reset if needed, and set
     * flag to resync timestamp and offset counters and send event
     * downstream */

    if (G_UNLIKELY(GST_BUFFER_IS_DISCONT(inbuf)
                   || GST_BUFFER_OFFSET(inbuf) != element->next_in_offset
                   || !GST_CLOCK_TIME_IS_VALID(element->t0))) {
        GST_DEBUG_OBJECT(element, "reset spstate");
        spiir_state_reset(element->spstate, element->num_depths,
                          element->stream);
        /* FIXME: need to push_drain of data in the adapter ? if upstream never
         * produces discontinous data, no need to push_drain. */
        gst_adapter_clear(element->adapter);

        element->need_discont = TRUE;

        /*
         * (re)sync timestamp and offset book-keeping. Set t0 and offset0 to be
         * the timestamp and offset of the inbuf.
         */

        element->t0              = GST_BUFFER_TIMESTAMP(inbuf);
        element->offset0         = GST_BUFFER_OFFSET(inbuf);
        element->num_gap_samples = 0;
        element->need_tail_drain = FALSE;
        element->samples_in      = 0;
        element->samples_out     = 0;
        if (element->num_head_cover_samples > 0)
            cuda_multiratespiir_update_exe_samples(
              &element->num_exe_samples, element->num_head_cover_samples);
        else
            cuda_multiratespiir_update_exe_samples(&element->num_exe_samples,
                                                   element->rate);
    }

    element->next_in_offset = GST_BUFFER_OFFSET_END(inbuf);

    /* 0-length buffers are produced to inform downstreams for current timestamp
     */
    if (size == 0) {
        /* time */
        if (GST_CLOCK_TIME_IS_VALID(element->t0)) {
            GST_BUFFER_TIMESTAMP(outbuf) =
              element->t0
              + gst_util_uint64_scale_int_round(element->samples_out,
                                                GST_SECOND, element->rate);
        } else {
            GST_BUFFER_TIMESTAMP(outbuf) = GST_CLOCK_TIME_NONE;
        }
        /* offset */
        if (element->offset0 != GST_BUFFER_OFFSET_NONE) {
            GST_BUFFER_OFFSET(outbuf) = element->offset0 + element->samples_out;
            GST_BUFFER_OFFSET_END(outbuf) = GST_BUFFER_OFFSET(outbuf);
        } else {
            GST_BUFFER_OFFSET(outbuf)     = GST_BUFFER_OFFSET_NONE;
            GST_BUFFER_OFFSET_END(outbuf) = GST_BUFFER_OFFSET_NONE;
        }

        GST_BUFFER_DURATION(outbuf) = 0;
        GST_BUFFER_SIZE(outbuf)     = GST_BUFFER_SIZE(inbuf);
        return GST_FLOW_OK;
    }

    gint in_samples, num_exe_samples, num_head_cover_samples,
      num_tail_cover_samples;
    in_samples             = GST_BUFFER_SIZE(inbuf) / (element->width / 8);
    num_exe_samples        = element->num_exe_samples;
    num_head_cover_samples = element->num_head_cover_samples;
    num_tail_cover_samples = element->num_tail_cover_samples;
    guint64 history_gap_samples, gap_buffer_len;
    gint num_zeros, adapter_len, num_filt_samples;
    gboolean is_gap;

    switch (element->gap_handle) {

    /* FIXME: case 1 may cause some bugs, have not tested it for a long time */
    case 1: // restart after gap

        /*
         * gap handling cuda_multiratespiir_get_available_samples (element)
         */

        if (GST_BUFFER_FLAG_IS_SET(inbuf, GST_BUFFER_FLAG_GAP)) {
            history_gap_samples = element->num_gap_samples;
            element->num_gap_samples += in_samples;

            /*
             * if receiving GAPs from the beginning, assemble same length GAPs
             */
            if (!element->need_tail_drain) {

                /*
                 * one gap buffer
                 */
                gap_buffer_len = in_samples;
                res            = cuda_multiratespiir_assemble_gap_buffer(
                  element, gap_buffer_len, outbuf);

                if (res != GST_FLOW_OK) return res;
                else
                    return GST_FLOW_OK;
            }

            /*
             * history is already cover the roll-offs,
             * produce the gap buffer
             */
            if (history_gap_samples >= (guint64)num_tail_cover_samples) {
                /*
                 * no process, gap buffer in place
                 */
                gap_buffer_len = in_samples;
                res            = cuda_multiratespiir_assemble_gap_buffer(
                  element, gap_buffer_len, outbuf);

                if (res != GST_FLOW_OK) return res;
            }

            /*
             * if receiving GAPs from some time later :
             * history number of gaps is not enough to cover the
             * total roll-offs of all the resamplers, check if current
             * number of gap samples will cover the roll-offs
             */
            if (history_gap_samples < (guint64)num_tail_cover_samples) {
                /*
                 * if current number of gap samples more than we can
                 * cover the roll-offs offset, process the buffer;
                 * otherwise absorb the inbuf
                 */
                if (element->num_gap_samples
                    >= (guint64)num_tail_cover_samples) {
                    /*
                     * one buffer to cover the roll-offs
                     */
                    num_zeros = num_tail_cover_samples - history_gap_samples;
                    adapter_push_zeros(element, num_zeros);
                    adapter_len =
                      cuda_multiratespiir_get_available_samples(element);
                    res = cuda_multiratespiir_push_drain(element, adapter_len);
                    if (res != GST_FLOW_OK) return res;

                    /*
                     * one gap buffer
                     */
                    gap_buffer_len = in_samples - num_zeros;
                    res            = cuda_multiratespiir_assemble_gap_buffer(
                      element, gap_buffer_len, outbuf);
                    if (res != GST_FLOW_OK) return res;

                } else {
                    /*
                     * if could not cover the roll-offs,
                     * absorb the buffer
                     */
                    num_zeros = in_samples;
                    adapter_push_zeros(element, num_zeros);
                    GST_INFO_OBJECT(element, "inbuf absorbed %d zero samples",
                                    num_zeros);
                    return GST_BASE_TRANSFORM_FLOW_DROPPED;
                }
            }
        }

        /*
         * inbuf is not gap
         */

        if (!GST_BUFFER_FLAG_IS_SET(inbuf, GST_BUFFER_FLAG_GAP)) {
            /*
             * history is gap, and gap samples has already cover the roll-offs,
             * reset spiir state
             * if history gap is smaller than a tail cover, continue processing.
             */
            if (element->num_gap_samples >= (guint64)num_tail_cover_samples) {
                if (element->need_tail_drain) {
                    adapter_len =
                      cuda_multiratespiir_get_available_samples(element);
                    cuda_multiratespiir_push_gap(
                      element, element->num_tail_cover_samples + adapter_len);
                    gst_adapter_clear(element->adapter);
                }
                spiir_state_reset(element->spstate, element->num_depths,
                                  element->stream);
                cuda_multiratespiir_update_exe_samples(
                  &element->num_exe_samples, element->num_head_cover_samples);
                num_exe_samples = element->num_exe_samples;
            }

            element->num_gap_samples = 0;
            element->need_tail_drain = TRUE;
            adapter_len = cuda_multiratespiir_get_available_samples(element);
            /*
             * here merely speed consideration: if samples ready to be processed
             * are less than num_exe_samples, wait until there are over
             * num_exe_samples
             */
            if (in_samples < num_exe_samples - adapter_len) {
                /* absorb the buffer */
                gst_buffer_ref(inbuf); /* don't let the adapter free it */
                gst_adapter_push(element->adapter, inbuf);
                GST_INFO_OBJECT(element, "inbuf absorbed %d samples",
                                in_samples);
                return GST_BASE_TRANSFORM_FLOW_DROPPED;

            } else {
                /*
                 * filter
                 */
                gst_buffer_ref(inbuf); /* don't let the adapter free it */
                gst_adapter_push(element->adapter, inbuf);
                /*
                 * to speed up, number of samples to be filtered is times of
                 * num_exe_samples
                 */
                adapter_len =
                  cuda_multiratespiir_get_available_samples(element);
                if (element->num_exe_samples == element->rate)
                    num_filt_samples =
                      gst_util_uint64_scale_int(adapter_len, 1, num_exe_samples)
                      * num_exe_samples;
                else
                    num_filt_samples = num_exe_samples;
                res = cuda_multiratespiir_process(element, num_filt_samples,
                                                  outbuf);

                if (res != GST_FLOW_OK) return res;
            }
        }
        break;

    case 0: // gap is treated as 0;
        is_gap =
          GST_BUFFER_FLAG_IS_SET(inbuf, GST_BUFFER_FLAG_GAP) ? TRUE : FALSE;
        add_flag_segment(
          element, GST_BUFFER_TIMESTAMP(inbuf),
          GST_BUFFER_TIMESTAMP(inbuf) + GST_BUFFER_DURATION(inbuf), is_gap);

        if (GST_BUFFER_FLAG_IS_SET(inbuf, GST_BUFFER_FLAG_GAP))
            adapter_push_zeros(element, in_samples);
        else {
            gst_buffer_ref(inbuf); /* don't let the adapter free it */
            gst_adapter_push(element->adapter, inbuf);
        }
        /*
         * to speed up, number of samples to be filtered is times of
         * num_exe_samples
         */
        adapter_len = cuda_multiratespiir_get_available_samples(element);
        g_assert(element->num_exe_samples > 0);
        if (adapter_len >= element->num_exe_samples) {
            if (element->num_depths > 1) element->need_tail_drain = TRUE;
            res = cuda_multiratespiir_process(element, element->num_exe_samples,
                                              outbuf);
            if (res != GST_FLOW_OK) return res;
        } else {
            GST_INFO_OBJECT(element, "inbuf absorbed %d samples", in_samples);
            return GST_BASE_TRANSFORM_FLOW_DROPPED;
        }
        break;

    default: GST_ERROR_OBJECT(element, "gap handling not supported"); break;
    }

    return GST_FLOW_OK;
}

static gboolean cuda_multiratespiir_event(GstBaseTransform *base,
                                          GstEvent *event) {
    CudaMultirateSPIIR *element = CUDA_MULTIRATESPIIR(base);

    switch (GST_EVENT_TYPE(event)) {
#if 0
    case GST_EVENT_FLUSH_STOP:
      cuda_multiratespiir_reset_spstate (element);
      if (element->state)
        element->funcs->skip_zeros (element->state);
      element->num_gap_samples = 0;
      element->need_tail_drain = FALSE;
      element->t0 = GST_CLOCK_TIME_NONE;
      element->in_offset0 = GST_BUFFER_OFFSET_NONE;
      element->out_offset0 = GST_BUFFER_OFFSET_NONE;
      element->samples_in = 0;
      element->samples_out = 0;
      element->need_discont = TRUE;
      break;

#endif
    case GST_EVENT_NEWSEGMENT:

        GST_DEBUG_OBJECT(element, "EVENT NEWSEGMENT");
        /* implicit assumption: spstate has been inited */
        if (element->need_tail_drain && element->num_tail_cover_samples > 0) {
            CUDA_CHECK(cudaSetDevice(element->deviceID));
            GST_DEBUG_OBJECT(element, "NEWSEGMENT, clear tails.");
            if (element->num_gap_samples >= element->num_tail_cover_samples) {
                cuda_multiratespiir_push_gap(element,
                                             element->num_tail_cover_samples);
            } else {
                adapter_push_zeros(element, element->num_tail_cover_samples);
                int adapter_len =
                  cuda_multiratespiir_get_available_samples(element);
                cuda_multiratespiir_push_drain(element, adapter_len);
            }

            spiir_state_reset(element->spstate, element->num_depths,
                              element->stream);
        }
        element->num_gap_samples = 0;
        element->need_tail_drain = FALSE;
        element->t0              = GST_CLOCK_TIME_NONE;
        element->offset0         = GST_BUFFER_OFFSET_NONE;
        element->next_in_offset  = GST_BUFFER_OFFSET_NONE;
        element->samples_in      = 0;
        element->samples_out     = 0;
        element->need_discont    = TRUE;
        g_mutex_lock(element->iir_bank_lock);
        if (!element->spstate)
            g_cond_wait(element->iir_bank_available, element->iir_bank_lock);
        if (element->num_head_cover_samples > 0)
            cuda_multiratespiir_update_exe_samples(
              &element->num_exe_samples, element->num_head_cover_samples);
        else
            cuda_multiratespiir_update_exe_samples(&element->num_exe_samples,
                                                   element->rate);
        g_mutex_unlock(element->iir_bank_lock);

        break;

    case GST_EVENT_EOS:

        GST_DEBUG_OBJECT(element, "EVENT EOS");
        if (element->need_tail_drain) {
            CUDA_CHECK(cudaSetDevice(element->deviceID));
            if (element->num_gap_samples >= element->num_tail_cover_samples) {
                GST_DEBUG_OBJECT(element,
                                 "EOS, clear tails by pushing gap, num gap "
                                 "samples %" G_GUINT64_FORMAT,
                                 element->num_gap_samples);
                cuda_multiratespiir_push_gap(element,
                                             element->num_tail_cover_samples);
            } else {

                GST_DEBUG_OBJECT(element, "EOS, clear tails by pushing drain");
                adapter_push_zeros(element, element->num_tail_cover_samples);
                int adapter_len =
                  cuda_multiratespiir_get_available_samples(element);
                cuda_multiratespiir_push_drain(element, adapter_len);
            }

            // spiir_state_reset (element->spstate, element->num_depths,
            // element->stream);
        }

        break;
    default: break;
    }

    return parent_class->event(base, event);
}

static void cuda_multiratespiir_set_property(GObject *object,
                                             guint prop_id,
                                             const GValue *value,
                                             GParamSpec *pspec) {
    CudaMultirateSPIIR *element;

    element          = CUDA_MULTIRATESPIIR(object);
    gboolean success = TRUE;

    GST_OBJECT_LOCK(element);
    switch (prop_id) {

    case PROP_IIRBANK_FNAME:

        GST_DEBUG("spiir bank acquiring the lock");
        g_mutex_lock(element->iir_bank_lock);
        GST_DEBUG("spiir bank have acquired the lock");

        GST_LOG_OBJECT(element, "obtaining bank, stream id is %d",
                       element->stream_id);
        element->bank_fname = g_value_dup_string(value);
        /* bank_id is deprecated, get the stream id directly from prop
         * must make sure stream_id has already loaded */
        // cuda_multiratespiir_read_bank_id(element->bank_fname,
        // &element->bank_id);

        int deviceCount;
        cudaGetDeviceCount(&deviceCount);
        element->deviceID = (element->stream_id) % deviceCount;
        GST_LOG("device for spiir %s %d\n", element->bank_fname,
                element->deviceID);
        CUDA_CHECK(cudaSetDevice(element->deviceID));
        // cudaStreamCreateWithFlags(&element->stream, cudaStreamNonBlocking);
        cudaStreamCreate(&element->stream);

        cuda_multiratespiir_read_ndepth_and_rate(
          element->bank_fname, &element->num_depths, &element->rate);

        cuda_multiratespiir_init_cover_samples(
          &element->num_head_cover_samples, &element->num_tail_cover_samples,
          element->rate, element->num_depths, DOWN_FILT_LEN * 2, UP_FILT_LEN);

        /* we consider the num_exe_samples equals to rate unless it is at the
         * first or last buffer */
        cuda_multiratespiir_update_exe_samples(&element->num_exe_samples,
                                               element->rate);

        element->spstate =
          spiir_state_create(element->bank_fname, element->num_depths,
                             element->rate, element->num_head_cover_samples,
                             element->num_exe_samples, element->stream);

        GST_DEBUG_OBJECT(element,
                         "number of cover samples set to (%d, %d), number of "
                         "exe samples set to %d",
                         element->num_head_cover_samples,
                         element->num_tail_cover_samples,
                         element->num_exe_samples);

        if (!element->spstate) {
            GST_ERROR_OBJECT(element, "spsate could not be initialised");
        }

        element->spstate_initialised = TRUE;

        /*
         * signal ready of the bank
         */
        element->outchannels = element->spstate[0]->num_templates * 2;
        element->width = 32; // FIXME: only can process float data
        GST_DEBUG_OBJECT(
          element, "spiir bank available, number of depths %d, outchannels %d",
          element->num_depths, element->outchannels);

        GST_DEBUG("spiir bank done read, broadcasting the lock");
        g_cond_broadcast(element->iir_bank_available);
        g_mutex_unlock(element->iir_bank_lock);
        GST_DEBUG("spiir bank done broadcasting");

        break;

    case PROP_GAP_HANDLE: element->gap_handle = g_value_get_int(value); break;

    case PROP_STREAM_ID: element->stream_id = g_value_get_int(value); break;

    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec); break;
    }
    GST_OBJECT_UNLOCK(element);
}

static void cuda_multiratespiir_get_property(GObject *object,
                                             guint prop_id,
                                             GValue *value,
                                             GParamSpec *pspec) {
    CudaMultirateSPIIR *element;

    element = CUDA_MULTIRATESPIIR(object);
    GST_OBJECT_LOCK(element);

    switch (prop_id) {
    case PROP_IIRBANK_FNAME:
        g_mutex_lock(element->iir_bank_lock);
        g_value_set_string(value, element->bank_fname);
        g_mutex_unlock(element->iir_bank_lock);
        break;

    case PROP_GAP_HANDLE: g_value_set_int(value, element->gap_handle); break;

    case PROP_STREAM_ID: g_value_set_int(value, element->stream_id); break;

    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec); break;
    }

    GST_OBJECT_UNLOCK(element);
}

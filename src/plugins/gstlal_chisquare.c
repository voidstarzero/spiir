/*
 * A \Chi^{2} element for the inspiral pipeline.
 *
 * Copyright (C) 2008  Kipp Cannon, Chad Hanna
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or (at your
 * option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
 */


/*
 * ========================================================================
 *
 *                                  Preamble
 *
 * ========================================================================
 */


/*
 * stuff from the C library
 */


#include <math.h>
#include <string.h>


/*
 * stuff from glib/gstreamer
 */


#include <glib.h>
#include <gst/gst.h>
#include <gst/base/gstcollectpads.h>


/*
 * stuff from GSL
 */


#include <gsl/gsl_complex.h>
#include <gsl/gsl_complex_math.h>
#include <gsl/gsl_matrix.h>
#include <gsl/gsl_blas.h>


/*
 * our own stuff
 */


#include <gstlal.h>
#include <gstlalcollectpads.h>
#include <gstlal_chisquare.h>


/*
 * ========================================================================
 *
 *                                 Parameters
 *
 * ========================================================================
 */


/* FIXME: Hard coded (for now) max degrees of freedom.  Why a max of 10 you
 * might ask?  Well For inspiral analysis we usually have about 5 different
 * pieces of the waveform (give or take a few).  So computing a 10 degree
 * chisq test on each gives 50 degrees of freedom total.  The std dev of
 * that chisq distribution is sqrt(50) and can be compared to the SNR^2
 * that we are trying to distinguish from a glitch.  That means we can
 * begin to have discriminatory power at SNR = 50^(1/4) = 2.66 which is in
 * the bulk of the SNR distribution expected from Gaussian noise - exactly
 * where we want to be*/

#define DEFAULT_MAX_DOF 10


/*
 * ========================================================================
 *
 *                             Utility Functions
 *
 * ========================================================================
 */


static int num_orthosnr_channels(const GSTLALChiSquare *element)
{
	return element->mixmatrix.matrix.size1;
}


static int num_snr_channels(const GSTLALChiSquare *element)
{
	return element->mixmatrix.matrix.size2;
}


static size_t mixmatrix_element_size(const GSTLALChiSquare *element)
{
	return sizeof(*element->mixmatrix.matrix.data);
}


/*
 * parse caps, and set bytes per sample on collect pads object
 */


static void set_bytes_per_sample(GstPad *pad, GstCaps *caps)
{
	GstStructure *structure;
	gint width, channels;

	structure = gst_caps_get_structure(caps, 0);
	gst_structure_get_int(structure, "width", &width);
	gst_structure_get_int(structure, "channels", &channels);

	gstlal_collect_pads_set_bytes_per_sample(pad, (width / 8) * channels);
}


/*
 * ============================================================================
 *
 *                              Caps --- SNR Pad
 *
 * ============================================================================
 */


/*
 * we can only accept caps that both ourselves and the downstream peer can
 * handle, and the number of channels must match the size of the mixing
 * matrix
 */


static GstCaps *getcaps_snr(GstPad *pad)
{
	GSTLALChiSquare *element = GSTLAL_CHISQUARE(GST_PAD_PARENT(pad));
	GstCaps *peercaps, *caps;

	GST_OBJECT_LOCK(element);

	/*
	 * start by retrieving our own caps.  use get_fixed_caps_func() to
	 * avoid recursing back into this function.
	 */

	caps = gst_pad_get_fixed_caps_func(pad);

	/*
	 * if we have a mixing matrix the sink pad's media type and sample
	 * width must be the same as the mixing matrix's, and the number of
	 * channels must match the number of columns in the mixing matrix.
	 */

	g_mutex_lock(element->mixmatrix_lock);
	if(element->mixmatrix_buf) {
		GstCaps *matrixcaps = gst_caps_make_writable(gst_buffer_get_caps(element->mixmatrix_buf));
		GstCaps *result;
		guint n;

		for(n = 0; n < gst_caps_get_size(matrixcaps); n++)
			gst_structure_set(gst_caps_get_structure(matrixcaps, n), "channels", G_TYPE_INT, num_snr_channels(element), NULL);
		result = gst_caps_intersect(matrixcaps, caps);
		gst_caps_unref(caps);
		gst_caps_unref(matrixcaps);
		caps = result;
	}
	g_mutex_unlock(element->mixmatrix_lock);

	/*
	 * now compute the intersection of the caps with the downstream
	 * peer's caps if known.
	 */

	peercaps = gst_pad_peer_get_caps(element->srcpad);
	if(peercaps) {
		GstCaps *result = gst_caps_intersect(peercaps, caps);
		gst_caps_unref(caps);
		gst_caps_unref(peercaps);
		caps = result;
	}

	/*
	 * done
	 */

	GST_OBJECT_UNLOCK(element);
	return caps;
}


/*
 * when setting new caps, extract the sample rate and bytes/sample from the
 * caps
 */


static gboolean setcaps_snr(GstPad *pad, GstCaps *caps)
{
	GSTLALChiSquare *element = GSTLAL_CHISQUARE(GST_PAD_PARENT(pad));
	GstStructure *structure;
	const char *media_type;
	gboolean result = TRUE;

	GST_OBJECT_LOCK(element);

	/*
	 * set bytes per sample on collect pads object
	 */

	set_bytes_per_sample(pad, caps);

	/*
	 * extract rate
	 */

	structure = gst_caps_get_structure(caps, 0);
	media_type = gst_structure_get_name(structure);
	gst_structure_get_int(structure, "rate", &element->rate);

	/*
	 * done
	 */

	GST_OBJECT_UNLOCK(element);
	return result;
}


/*
 * ============================================================================
 *
 *                        Caps --- Orthogonal SNR Pad
 *
 * ============================================================================
 */


/*
 * we can only accept caps that both ourselves and the downstream peer can
 * handle, and the number of channels must match the size of the mixing
 * matrix
 */


static GstCaps *getcaps_orthosnr(GstPad *pad)
{
	GSTLALChiSquare *element = GSTLAL_CHISQUARE(GST_PAD_PARENT(pad));
	GstCaps *result, *snrcaps, *caps;

	GST_OBJECT_LOCK(element);

	/*
	 * start by retrieving our own caps.  use get_fixed_caps_func() to
	 * avoid recursing back into this function.
	 */

	caps = gst_pad_get_fixed_caps_func(pad);

	/*
	 * now intersect with the SNR pad's caps, replacing the number of
	 * channels with the number of rows in the mixing matrix if known.
	 * Retrieving the SNR pad's caps in-turn performs an intersection
	 * with the down-stream peer so we don't have to do that here
	 */

	GST_OBJECT_UNLOCK(element);
	snrcaps = gst_pad_get_caps(element->snrpad);
	GST_OBJECT_LOCK(element);

	g_mutex_lock(element->mixmatrix_lock);
	if(element->mixmatrix_buf) {
		guint n;
		for(n = 0; n < gst_caps_get_size(snrcaps); n++)
			gst_structure_set(gst_caps_get_structure(snrcaps, n), "channels", G_TYPE_INT, num_orthosnr_channels(element), NULL);
	}
	g_mutex_unlock(element->mixmatrix_lock);

	result = gst_caps_intersect(snrcaps, caps);
	gst_caps_unref(caps);
	gst_caps_unref(snrcaps);

	/*
	 * done
	 */

	GST_OBJECT_UNLOCK(element);
	return result;
}


/*
 * when setting new caps, extract the sample rate and bytes/sample from the
 * caps
 */


static gboolean setcaps_orthosnr(GstPad *pad, GstCaps *caps)
{
	GSTLALChiSquare *element = GSTLAL_CHISQUARE(GST_PAD_PARENT(pad));

	GST_OBJECT_LOCK(element);

	/*
	 * set bytes per sample on collect pads object
	 */

	set_bytes_per_sample(pad, caps);

	/*
	 * extract rate
	 */

	/* FIXME:  check that it is allowed */

	/*
	 * done
	 */

	GST_OBJECT_UNLOCK(element);
	return TRUE;
}


/*
 * ============================================================================
 *
 *                                 Matrix Pad
 *
 * ============================================================================
 */


/*
 * setcaps()
 */


static gboolean setcaps_matrix(GstPad *pad, GstCaps *caps)
{
	GSTLALChiSquare *element = GSTLAL_CHISQUARE(GST_PAD_PARENT(pad));
	gboolean result = TRUE;

	GST_OBJECT_LOCK(element);

	/*
	 * done
	 */

	GST_OBJECT_UNLOCK(element);
	return result;
}


/*
 * chain()
 */


static GstFlowReturn chain_matrix(GstPad *pad, GstBuffer *sinkbuf)
{
	GSTLALChiSquare *element = GSTLAL_CHISQUARE(GST_PAD_PARENT(pad));
	GstCaps *caps = gst_buffer_get_caps(sinkbuf);
	GstStructure *structure = gst_caps_get_structure(caps, 0);
	GstFlowReturn result = GST_FLOW_OK;
	gint rows, cols;

	GST_OBJECT_LOCK(element);

	/*
	 * get the matrix size
	 */

	gst_structure_get_int(structure, "channels", &cols);
	rows = GST_BUFFER_SIZE(sinkbuf) / mixmatrix_element_size(element) / cols;
	if(rows * cols * mixmatrix_element_size(element) != GST_BUFFER_SIZE(sinkbuf)) {
		GST_ERROR_OBJECT(element, "buffer size mismatch:  input buffer size not divisible by the channel count");
		gst_buffer_unref(sinkbuf);
		result = GST_FLOW_NOT_NEGOTIATED;
		goto done;
	}

	/*
	 * replace the current matrix with the new one
	 */

	g_mutex_lock(element->mixmatrix_lock);
	if(element->mixmatrix_buf)
		gst_buffer_unref(element->mixmatrix_buf);
	element->mixmatrix_buf = sinkbuf;
	element->mixmatrix = gsl_matrix_view_array((double *) GST_BUFFER_DATA(sinkbuf), rows, cols);
	g_cond_signal(element->mixmatrix_available);
	g_mutex_unlock(element->mixmatrix_lock);

	/*
	 * done
	 */

done:
	gst_caps_unref(caps);
	GST_OBJECT_UNLOCK(element);
	return result;
}


/*
 * ============================================================================
 *
 *                            \Chi^{2} Computation
 *
 * ============================================================================
 */


static GstFlowReturn collected(GstCollectPads *pads, gpointer user_data)
{
	GSTLALChiSquare *element = GSTLAL_CHISQUARE(user_data);
	guint64 earliest_input_offset, earliest_input_offset_end;
	guint sample, length;
	GstBuffer *buf;
	GstBuffer *orthosnrbuf;
	gint dof;
	gint ortho_channel, numorthochannels;
	gint channel, numchannels;

	/*
	 * get the range of offsets (in the output stream) spanned by the
	 * available input buffers.
	 */

	if(!gstlal_collect_pads_get_earliest_offsets(element->collect, &earliest_input_offset, &earliest_input_offset_end, element->rate, element->output_timestamp_at_zero)) {
		GST_ERROR_OBJECT(element, "cannot deduce input timestamp offset information");
		return GST_FLOW_ERROR;
	}

	/*
	 * check for EOS
	 */

	if(earliest_input_offset == GST_BUFFER_OFFSET_NONE)
		goto eos;

	/*
	 * don't let time go backwards.  in principle we could be smart and
	 * handle this, but the audiorate element can be used to correct
	 * screwed up time series so there is no point in re-inventing its
	 * capabilities here.
	 */

	if(earliest_input_offset < element->output_offset) {
		GST_ERROR_OBJECT(element, "detected time reversal in at least one input stream:  expected nothing earlier than offset %llu, found sample at offset %llu", (unsigned long long) element->output_offset, (unsigned long long) earliest_input_offset);
		return GST_FLOW_ERROR;
	}

	/*
	 * get buffers upto the desired end offset.
	 */

	buf = gstlal_collect_pads_take_buffer(pads, element->snrcollectdata, earliest_input_offset_end);
	orthosnrbuf = gstlal_collect_pads_take_buffer(pads, element->orthosnrcollectdata, earliest_input_offset_end);

	/*
	 * NULL means EOS.
	 */
 
	if(!buf || !orthosnrbuf) {
		/* FIXME:  handle EOS */
	}

	/*
	 * FIXME:  rethink the collect pads system so that this doesn't
	 * happen  (I think the second part already cannot happen because
	 * we get the collect pads system to tell us the upper bound of
	 * available offsets before asking for the buffers, but need to
	 * check this)
	 */

	if(GST_BUFFER_OFFSET(buf) != GST_BUFFER_OFFSET(orthosnrbuf) || GST_BUFFER_OFFSET_END(buf) != GST_BUFFER_OFFSET_END(orthosnrbuf)) {
		gst_buffer_unref(buf);
		gst_buffer_unref(orthosnrbuf);
		GST_ERROR_OBJECT(element, "misaligned buffer boundaries");
		return GST_FLOW_ERROR;
	}

	/*
	 * Gap --> pass-through
	 */

	if(GST_BUFFER_FLAG_IS_SET(buf, GST_BUFFER_FLAG_GAP) || GST_BUFFER_FLAG_IS_SET(orthosnrbuf, GST_BUFFER_FLAG_GAP)) {
		memset(GST_BUFFER_DATA(buf), 0, GST_BUFFER_SIZE(buf));
		gst_buffer_unref(orthosnrbuf);
		return gst_pad_push(element->srcpad, buf);
	}

	/*
	 * compute the number of samples in each channel
	 */

	length = GST_BUFFER_OFFSET_END(buf) - GST_BUFFER_OFFSET(buf);

	/*
	 * make sure the mix matrix is available, wait until it is
	 */

	g_mutex_lock(element->mixmatrix_lock);
	if(!element->mixmatrix_buf) {
		g_cond_wait(element->mixmatrix_available, element->mixmatrix_lock);
		if(!element->mixmatrix_buf) {
			/* mixing matrix didn't get set.  probably means
			 * we're being disposed(). */
			g_mutex_unlock(element->mixmatrix_lock);
			gst_buffer_unref(buf);
			gst_buffer_unref(orthosnrbuf);
			GST_ERROR_OBJECT(element, "no mixing matrix available");
			return GST_FLOW_NOT_NEGOTIATED;
		}
	}

	/*
	 * compute the \Chi^{2} values in-place in the input buffer
	 */
	/* FIXME:  Assumes that the most important basis vectors are at the
	 * beginning this is a sensible assumption */
	/* FIXME: do with gsl functions?? */

	numorthochannels = (guint) num_orthosnr_channels(element);
	numchannels = (guint) num_snr_channels(element);
	dof = (numorthochannels < element->max_dof) ? numorthochannels : element->max_dof;

	for(sample = 0; sample < length; sample++) {
		double *data = &((double *) GST_BUFFER_DATA(buf))[numchannels * sample];
		const double *orthodata = &((double *) GST_BUFFER_DATA(orthosnrbuf))[numorthochannels * sample];
		for(channel = 0; channel < numchannels; channel++) {
			double snr = data[channel];
			data[channel] = 0;
			for(ortho_channel = 0; ortho_channel < dof; ortho_channel++)
				data[channel] += pow(orthodata[ortho_channel] / gsl_matrix_get(&element->mixmatrix.matrix, ortho_channel, channel) - snr, 2.0);
			
		}
	}
	g_mutex_unlock(element->mixmatrix_lock);

	/*
	 * push the buffer downstream
	 */

	gst_buffer_unref(orthosnrbuf);
	return gst_pad_push(element->srcpad, buf);

eos:
	GST_DEBUG_OBJECT(element, "no data available (EOS)");
	gst_pad_push_event(element->srcpad, gst_event_new_eos());
	return GST_FLOW_UNEXPECTED;
}


/*
 * ============================================================================
 *
 *                                Type Support
 *
 * ============================================================================
 */


/*
 * Parent class.
 */


static GstElementClass *parent_class = NULL;


/*
 * Instance finalize function.  See ???
 */


static void finalize(GObject *object)
{
	GSTLALChiSquare *element = GSTLAL_CHISQUARE(object);

	gst_object_unref(element->orthosnrpad);
	element->orthosnrpad = NULL;
	gst_object_unref(element->snrpad);
	element->snrpad = NULL;
	gst_object_unref(element->matrixpad);
	element->matrixpad = NULL;
	gst_object_unref(element->srcpad);
	element->srcpad = NULL;

	gst_object_unref(element->collect);
	element->orthosnrcollectdata = NULL;
	element->snrcollectdata = NULL;
	element->collect = NULL;

	g_mutex_free(element->mixmatrix_lock);
	element->mixmatrix_lock = NULL;
	g_cond_free(element->mixmatrix_available);
	element->mixmatrix_available = NULL;
	if(element->mixmatrix_buf) {
		gst_buffer_unref(element->mixmatrix_buf);
		element->mixmatrix_buf = NULL;
	}

	G_OBJECT_CLASS(parent_class)->finalize(object);
}


/*
 * change state
 */


static GstStateChangeReturn change_state(GstElement * element, GstStateChange transition)
{
	GSTLALChiSquare *chisquare = GSTLAL_CHISQUARE(element);

	switch(transition) {
	case GST_STATE_CHANGE_READY_TO_PAUSED:
		chisquare->output_offset = 0;
		chisquare->output_timestamp_at_zero = 0;
		break;

	default:
		break;
	}

	return parent_class->change_state(element, transition);
}


/*
 * Base init function.  See
 *
 * http://developer.gnome.org/doc/API/2.0/gobject/gobject-Type-Information.html#GBaseInitFunc
 */


static void base_init(gpointer class)
{
	static GstElementDetails plugin_details = {
		"Inspiral \\Chi^{2}",
		"Filter",
		"A \\Chi^{2} statistic for the inspiral pipeline",
		"Kipp Cannon <kcannon@ligo.caltech.edu>, Chad Hanna <channa@ligo.caltech.edu>"
	};
	GstElementClass *element_class = GST_ELEMENT_CLASS(class);

	gst_element_class_set_details(element_class, &plugin_details);

	gst_element_class_add_pad_template(
		element_class,
		gst_pad_template_new(
			"matrix",
			GST_PAD_SINK,
			GST_PAD_ALWAYS,
			gst_caps_from_string(
				"audio/x-raw-float, " \
				"channels = (int) [ 1, MAX ], " \
				"endianness = (int) BYTE_ORDER, " \
				"width = (int) 64"
			)
		)
	);
	gst_element_class_add_pad_template(
		element_class,
		gst_pad_template_new(
			"orthosnr",
			GST_PAD_SINK,
			GST_PAD_ALWAYS,
			gst_caps_from_string(
				"audio/x-raw-float, " \
				"rate = (int) [ 1, MAX ], " \
				"channels = (int) [ 1, MAX ], " \
				"endianness = (int) BYTE_ORDER, " \
				"width = (int) 64"
			)
		)
	);
	gst_element_class_add_pad_template(
		element_class,
		gst_pad_template_new(
			"snr",
			GST_PAD_SINK,
			GST_PAD_ALWAYS,
			gst_caps_from_string(
				"audio/x-raw-float, " \
				"rate = (int) [ 1, MAX ], " \
				"channels = (int) [ 1, MAX ], " \
				"endianness = (int) BYTE_ORDER, " \
				"width = (int) 64"
			)
		)
	);
	gst_element_class_add_pad_template(
		element_class,
		gst_pad_template_new(
			"src",
			GST_PAD_SRC,
			GST_PAD_ALWAYS,
			gst_caps_from_string(
				"audio/x-raw-float, " \
				"rate = (int) [ 1, MAX ], " \
				"channels = (int) [ 1, MAX ], " \
				"endianness = (int) BYTE_ORDER, " \
				"width = (int) 64"
			)
		)
	);
}


/*
 * Class init function.  See
 *
 * http://developer.gnome.org/doc/API/2.0/gobject/gobject-Type-Information.html#GClassInitFunc
 */


static void class_init(gpointer klass, gpointer class_data)
{
	GObjectClass *gobject_class = G_OBJECT_CLASS(klass);
	GstElementClass *gstelement_class = GST_ELEMENT_CLASS(klass);

	parent_class = g_type_class_ref(GST_TYPE_ELEMENT);

	gobject_class->finalize = finalize;

	gstelement_class->change_state = change_state;
}


/*
 * Instance init function.  See
 *
 * http://developer.gnome.org/doc/API/2.0/gobject/gobject-Type-Information.html#GInstanceInitFunc
 */


static void instance_init(GTypeInstance *object, gpointer class)
{
	GSTLALChiSquare *element = GSTLAL_CHISQUARE(object);
	GstPad *pad;

	gst_element_create_all_pads(GST_ELEMENT(element));
	element->collect = gst_collect_pads_new();
	gst_collect_pads_set_function(element->collect, collected, element);

	/* configure (and ref) matrix pad */
	pad = gst_element_get_static_pad(GST_ELEMENT(element), "matrix");
	gst_pad_set_setcaps_function(pad, setcaps_matrix);
	gst_pad_set_chain_function(pad, chain_matrix);
	element->matrixpad = pad;

	/* configure (and ref) orthogonal SNR sink pad */
	pad = gst_element_get_static_pad(GST_ELEMENT(element), "orthosnr");
	gst_pad_set_getcaps_function(pad, getcaps_orthosnr);
	gst_pad_set_setcaps_function(pad, setcaps_orthosnr);
	element->orthosnrcollectdata = gstlal_collect_pads_add_pad(element->collect, pad, sizeof(*element->orthosnrcollectdata));
	element->orthosnrpad = pad;

	/* configure (and ref) SNR sink pad */
	pad = gst_element_get_static_pad(GST_ELEMENT(element), "snr");
	gst_pad_set_getcaps_function(pad, getcaps_snr);
	gst_pad_set_setcaps_function(pad, setcaps_snr);
	element->snrcollectdata = gstlal_collect_pads_add_pad(element->collect, pad, sizeof(*element->snrcollectdata));
	element->snrpad = pad;

	/* retrieve (and ref) src pad */
	element->srcpad = gst_element_get_static_pad(GST_ELEMENT(element), "src");

	/* internal data */
	element->rate = 0;
	element->max_dof = DEFAULT_MAX_DOF;
	element->mixmatrix_lock = g_mutex_new();
	element->mixmatrix_available = g_cond_new();
	element->mixmatrix_buf = NULL;
}


/*
 * gstlal_chisquare_get_type().
 */


GType gstlal_chisquare_get_type(void)
{
	static GType type = 0;

	if(!type) {
		static const GTypeInfo info = {
			.class_size = sizeof(GSTLALChiSquareClass),
			.class_init = class_init,
			.base_init = base_init,
			.instance_size = sizeof(GSTLALChiSquare),
			.instance_init = instance_init,
		};
		type = g_type_register_static(GST_TYPE_ELEMENT, "lal_chisquare", &info, 0);
	}

	return type;
}

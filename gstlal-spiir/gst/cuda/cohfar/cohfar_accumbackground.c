/*
 * Copyright (C) 2015	Qi Chu	<qi.chu@uwa.edu.au>,
 *               2020   Tom Almeida <tom@tommoa.me>,
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
 */

/*
 * ============================================================================
 *
 *                                  Preamble
 *
 * ============================================================================
 */
#include <math.h>
#include <string.h>
/*
 *  stuff from gobject/gstreamer
 */

#include <glib.h>
#include <gst/base/gstbasetransform.h>
#include <gst/gst.h>
#include <gstlal/gstlal.h>

/*
 * stuff from here
 */

#include <cohfar/background_stats_utils.h>
#include <cohfar/cohfar_accumbackground.h>
#include <pipe_macro.h>
#include <postcohtable.h>
#include <time.h>
#define NOT_INIT            -1
#define DEFAULT_STATS_FNAME "stats.xml.gz"
/*
 * ============================================================================
 *
 *                                 Utilities
 *
 * ============================================================================
 */

/*
 * ============================================================================
 *
 *                           GStreamer Boiler Plate
 *
 * ============================================================================
 */

#define GST_CAT_DEFAULT cohfar_accumbackground_debug
GST_DEBUG_CATEGORY_STATIC(GST_CAT_DEFAULT);

static void additional_initializations(GType type) {
    GST_DEBUG_CATEGORY_INIT(GST_CAT_DEFAULT, "cohfar_accumbackground", 0,
                            "cohfar_accumbackground element");
}

GST_BOILERPLATE_FULL(CohfarAccumbackground,
                     cohfar_accumbackground,
                     GstElement,
                     GST_TYPE_ELEMENT,
                     additional_initializations);

enum property {
    PROP_0,
    PROP_IFOS,
    PROP_HIST_TRIALS,
    PROP_SOURCE_TYPE,
    PROP_SNAPSHOT_INTERVAL,
    PROP_HISTORY_FNAME,
    PROP_OUTPUT_PREFIX,
    PROP_OUTPUT_NAME
};

static void cohfar_accumbackground_set_property(GObject *object,
                                                guint prop_id,
                                                const GValue *value,
                                                GParamSpec *pspec);
static void cohfar_accumbackground_get_property(GObject *object,
                                                guint prop_id,
                                                GValue *value,
                                                GParamSpec *pspec);

/* vmethods */

static GstFlowReturn cohfar_accumbackground_chain(GstPad *pad,
                                                  GstBuffer *inbuf);
static gboolean cohfar_accumbackground_sink_event(GstPad *pad, GstEvent *event);
static void cohfar_accumbackground_dispose(GObject *object);

static void update_stats_icombo(PostcohInspiralTable *intable,
                                TriggerStatsXML *stats) {
    int nifo, ifo;
    nifo = __builtin_popcount(stats->icombo + 1);

    if (stats->icombo > -1) {
        // update the multi-IFO background at the last bin.
        trigger_stats_feature_rate_update(
          (double)(intable->cohsnr), (double)intable->cmbchisq,
          stats->multistats[nifo]->feature, stats->multistats[nifo]);

        /* add single detector stats */
        // update single-IFO background according the single-IFO decomposition
        int index;
        for (ifo = 0, index = 0; ifo < MAX_NIFO; ifo++) {
            if ((stats->icombo + 1) & (1 << ifo)) {
                trigger_stats_feature_rate_update(
                  (double)((&intable->snglsnr_H)[ifo]),
                  (double)((&intable->chisq_H)[ifo]),
                  stats->multistats[index]->feature, stats->multistats[index]);
                ++index;
            }
        }
    }
}

/*
 * ============================================================================
 *
 *                     GstElement Method Overrides
 *
 * ============================================================================
 */

/*
 * chain()
 */

static GstFlowReturn cohfar_accumbackground_chain(GstPad *pad,
                                                  GstBuffer *inbuf) {
    CohfarAccumbackground *element =
      COHFAR_ACCUMBACKGROUND(GST_OBJECT_PARENT(pad));
    GstFlowReturn result = GST_FLOW_OK;

    GST_LOG_OBJECT(
      element,
      "receiving accum %s+%s buffer of %" G_GSIZE_FORMAT
      " bytes, ts %" GST_TIME_FORMAT ", duration %" GST_TIME_FORMAT
      ", offset %" G_GINT64_FORMAT ", offset_end %" G_GINT64_FORMAT,
      GST_BUFFER_FLAG_IS_SET(inbuf, GST_BUFFER_FLAG_GAP) ? "GAP" : "NONGAP",
      GST_BUFFER_IS_DISCONT(inbuf) ? "DISCONT" : "CONT", GST_BUFFER_SIZE(inbuf),
      GST_TIME_ARGS(GST_BUFFER_TIMESTAMP(inbuf)),
      GST_TIME_ARGS(GST_BUFFER_DURATION(inbuf)), GST_BUFFER_OFFSET(inbuf),
      GST_BUFFER_OFFSET_END(inbuf));

    if (!GST_CLOCK_TIME_IS_VALID(element->t_roll_start))
        element->t_roll_start = GST_BUFFER_TIMESTAMP(inbuf);

    /*
     * initialize stats files
     */
    TriggerStatsXML *bgstats = element->bgstats;
    TriggerStatsXML *zlstats = element->zlstats;
    // TriggerStats **stats_prompt = element->stats_prompt;
    // TriggerStatsPointerList *stats_list = element->stats_list;
    // /* reset stats_prompt */
    // trigger_stats_reset(stats_prompt, element->nifo);

    /*
     * reset stats in the stats_list in order to input new background points
     */
    // int pos = stats_list->pos;
    // TriggerStats **cur_stats_in_list = stats_list->plist[pos];
    // trigger_stats_reset(cur_stats_in_list, element->nifo);

    /*
     * calculate number of output postcoh entries
     */
    int outentries = 0;

    PostcohInspiralTable *intable =
      (PostcohInspiralTable *)GST_BUFFER_DATA(inbuf);
    PostcohInspiralTable *intable_end =
      (PostcohInspiralTable *)(GST_BUFFER_DATA(inbuf) + GST_BUFFER_SIZE(inbuf));
    for (; intable < intable_end; intable++)
        if (intable->is_background == FLAG_FOREGROUND
            || intable->is_background == FLAG_EMPTY)
            outentries++;

    /*
     * allocate output buffer
     */
    GstBuffer *outbuf = NULL;
    GstPad *srcpad    = element->srcpad;
    GstCaps *caps     = GST_PAD_CAPS(srcpad);

    /* allocate extra space for prompt stats */
    // int out_size = sizeof(PostcohInspiralTable) * outentries +
    // sizeof(TriggerStats) * (nifo + 1);
    int out_size = sizeof(PostcohInspiralTable) * outentries;
    result       = gst_pad_alloc_buffer(srcpad, 0, out_size, caps, &outbuf);
    if (result != GST_FLOW_OK) {
        GST_ERROR_OBJECT(
          srcpad, "Could not allocate postcoh-inspiral buffer %d", result);
        return result;
    }
    int icombo = 0;

    /*
     * update background rate
     */

    intable = (PostcohInspiralTable *)GST_BUFFER_DATA(inbuf);
    PostcohInspiralTable *outtable =
      (PostcohInspiralTable *)GST_BUFFER_DATA(outbuf);
    int isingle, nifo;
    for (; intable < intable_end; intable++) {
        icombo = get_icombo(intable->ifos);
        // The combination of IFOs is invalid
        if (icombo < 0) {
            LIGOTimeGPS ligo_time;
            XLALINT8NSToGPS(&ligo_time, GST_BUFFER_TIMESTAMP(inbuf));
            fprintf(stderr,
                    "invalid ifo combo in cohfar_accumbackground at GPS %d, "
                    "outentries %d, table flag %d, cohsnr %f\n",
                    ligo_time.gpsSeconds, outentries, intable->is_background,
                    intable->cohsnr);
        }
        if (intable->is_background == FLAG_BACKGROUND) {
            update_stats_icombo(
              intable,
              bgstats); // update the last combination and single IFO stats
        } else if (intable->is_background
                   == FLAG_FOREGROUND) { /* coherent trigger entry */
            update_stats_icombo(
              intable,
              zlstats); // update the last combination and single IFO stats
            memcpy(outtable, intable, sizeof(PostcohInspiralTable));
            outtable++;
        } else {
            /* increment livetime if participating nifo >= 2 */
            // If icombo is a power of two, then there is only one participating
            // IFO, thus we can use the property of `(x & (x-1)) != 0` to
            // determine if we have more than one IFO participating
            //
            // Note that this is inverted because icombo is sum(1 << index) - 1
            if ((icombo + 1) & icombo) {
                nifo = __builtin_popcount(icombo + 1);
                /* add single detector stats */
                get_write_ifo_mapping(IFOComboMap[icombo].name, nifo,
                                      element->write_ifo_mapping);

                for (isingle = 0; isingle <= nifo; isingle++) {
                    trigger_stats_livetime_inc(bgstats->multistats, isingle);
                    trigger_stats_livetime_inc(zlstats->multistats, isingle);
                }
            }
            memcpy(outtable, intable, sizeof(PostcohInspiralTable));
            outtable++;
        }
    }
    /*
     * calculate immediate PDF using stats_prompt from stats_list
     */

    /*
     * shuffle one step down in stats_list
     */

    /* snapshot background xml file when reaching the snapshot point*/
    GstClockTime t_cur = GST_BUFFER_TIMESTAMP(inbuf);
    element->t_end     = t_cur;
    gint duration =
      (int)((element->t_end - element->t_roll_start) / GST_SECOND);
    if (element->snapshot_interval > 0
        && duration >= element->snapshot_interval) {
        gint gps_time      = (int)(element->t_roll_start / GST_SECOND);
        GString *fname     = g_string_new(element->output_prefix);
        GString *tmp_fname = g_string_new(element->output_prefix);
        g_string_append_printf(fname, "_%d_%d.xml.gz", gps_time, duration);
        g_string_append_printf(tmp_fname, "_%d_%d.xml.gz_next", gps_time,
                               duration);
        trigger_stats_xml_dump(element->bgstats, element->hist_trials,
                               tmp_fname->str, STATS_XML_WRITE_START,
                               &(element->stats_writer));
        trigger_stats_xml_dump(element->zlstats, element->hist_trials,
                               tmp_fname->str, STATS_XML_WRITE_MID,
                               &(element->stats_writer));
        trigger_stats_xml_dump(element->sgstats, element->hist_trials,
                               tmp_fname->str, STATS_XML_WRITE_END,
                               &(element->stats_writer));
        printf("rename from %s\n", tmp_fname->str);
        if (g_rename(tmp_fname->str, fname->str) != 0) {
            fprintf(stderr, "unable to rename to %s\n", fname->str);
            return GST_FLOW_ERROR;
        }
        g_string_free(fname, TRUE);
        g_string_free(tmp_fname, TRUE);
        trigger_stats_xml_reset(element->bgstats);
        trigger_stats_xml_reset(element->zlstats);
        element->t_roll_start = t_cur;
    }

    /*
     * set the outbuf meta data
     */
    GST_BUFFER_TIMESTAMP(outbuf)  = GST_BUFFER_TIMESTAMP(inbuf);
    GST_BUFFER_DURATION(outbuf)   = GST_BUFFER_DURATION(inbuf);
    GST_BUFFER_OFFSET(outbuf)     = GST_BUFFER_OFFSET(inbuf);
    GST_BUFFER_OFFSET_END(outbuf) = GST_BUFFER_OFFSET_END(inbuf);
    GST_BUFFER_SIZE(outbuf)       = sizeof(PostcohInspiralTable) * outentries;
    if (GST_BUFFER_FLAG_IS_SET(inbuf, GST_BUFFER_FLAG_GAP))
        GST_BUFFER_FLAG_SET(outbuf, GST_BUFFER_FLAG_GAP);

    gst_buffer_unref(inbuf);
    result = gst_pad_push(srcpad, outbuf);

    GST_LOG_OBJECT(
      element,
      "pushed %s+%s buffer of %ld bytes, ts %" GST_TIME_FORMAT
      ", duration %" GST_TIME_FORMAT ", offset %" G_GINT64_FORMAT
      ", offset_end %" G_GINT64_FORMAT,
      GST_BUFFER_FLAG_IS_SET(outbuf, GST_BUFFER_FLAG_GAP) ? "GAP" : "NONGAP",
      GST_BUFFER_IS_DISCONT(outbuf) ? "DISCONT" : "CONT",
      GST_BUFFER_SIZE(outbuf), GST_TIME_ARGS(GST_BUFFER_TIMESTAMP(outbuf)),
      GST_TIME_ARGS(GST_BUFFER_DURATION(outbuf)), GST_BUFFER_OFFSET(outbuf),
      GST_BUFFER_OFFSET_END(outbuf));

    return result;
}

/*
 * ============================================================================
 *
 *                          GObject Method Overrides
 *
 * ============================================================================
 */

/* handle events (search) */
static gboolean cohfar_accumbackground_sink_event(GstPad *pad,
                                                  GstEvent *event) {
    CohfarAccumbackground *element =
      COHFAR_ACCUMBACKGROUND(GST_OBJECT_PARENT(pad));

    switch (GST_EVENT_TYPE(event)) {
    case GST_EVENT_EOS:
        //      if (fflush (sink->file))
        //        goto flush_failed;

        GST_LOG_OBJECT(element, "EVENT EOS. ");
        if (element->snapshot_interval > 0) {
            gint gps_time = (int)(element->t_roll_start / GST_SECOND);
            gint duration =
              (int)((element->t_end - element->t_roll_start) / GST_SECOND);
            GString *fname     = g_string_new(element->output_prefix);
            GString *tmp_fname = g_string_new(element->output_prefix);
            g_string_append_printf(fname, "_%d_%d.xml.gz", gps_time, duration);
            g_string_append_printf(tmp_fname, "_%d_%d.xml.gz_next", gps_time,
                                   duration);
            trigger_stats_xml_dump(element->bgstats, element->hist_trials,
                                   tmp_fname->str, STATS_XML_WRITE_START,
                                   &(element->stats_writer));
            trigger_stats_xml_dump(element->zlstats, element->hist_trials,
                                   tmp_fname->str, STATS_XML_WRITE_MID,
                                   &(element->stats_writer));
            trigger_stats_xml_dump(element->sgstats, element->hist_trials,
                                   tmp_fname->str, STATS_XML_WRITE_END,
                                   &(element->stats_writer));
            printf("rename from %s\n", tmp_fname->str);
            g_rename(tmp_fname->str, fname->str);
            g_string_free(fname, TRUE);
            g_string_free(tmp_fname, TRUE);

        } else {
            GString *fname = g_string_new(element->output_name);
            trigger_stats_xml_dump(element->bgstats, element->hist_trials,
                                   fname->str, STATS_XML_WRITE_START,
                                   &(element->stats_writer));
            trigger_stats_xml_dump(element->zlstats, element->hist_trials,
                                   fname->str, STATS_XML_WRITE_MID,
                                   &(element->stats_writer));
            trigger_stats_xml_dump(element->sgstats, element->hist_trials,
                                   fname->str, STATS_XML_WRITE_END,
                                   &(element->stats_writer));
            g_string_free(fname, TRUE);
        }

        break;
    default: break;
    }

    return gst_pad_event_default(pad, event);
}

/*
 * set_property()
 */

static void cohfar_accumbackground_set_property(GObject *object,
                                                enum property prop_id,
                                                const GValue *value,
                                                GParamSpec *pspec) {
    CohfarAccumbackground *element = COHFAR_ACCUMBACKGROUND(object);

    GST_OBJECT_LOCK(element);
    switch (prop_id) {
    case PROP_IFOS:
        element->ifos   = g_value_dup_string(value);
        element->nifo   = strlen(element->ifos) / IFO_LEN;
        element->icombo = get_icombo(element->ifos);
        element->bgstats =
          trigger_stats_xml_create(element->ifos, STATS_XML_TYPE_BACKGROUND);
        element->zlstats =
          trigger_stats_xml_create(element->ifos, STATS_XML_TYPE_ZEROLAG);
        element->sgstats =
          trigger_stats_xml_create(element->ifos, STATS_XML_TYPE_SIGNAL);
        break;

    case PROP_SOURCE_TYPE:
        /* must make sure ifos have been loaded, so stats have been created */
        g_assert(element->ifos != NULL);
        element->source_type = g_value_get_int(value);
        signal_stats_init(element->sgstats, element->source_type);
        break;

    case PROP_HISTORY_FNAME:

        /* must make sure ifos have been loaded, so stats have been created */
        g_assert(element->ifos != NULL);
        element->history_fname = g_value_dup_string(value);
        trigger_stats_xml_from_xml(element->bgstats, &(element->hist_trials),
                                   element->history_fname);
        break;

    case PROP_OUTPUT_NAME:
        element->output_name = g_value_dup_string(value);
        break;

    case PROP_OUTPUT_PREFIX:
        element->output_prefix = g_value_dup_string(value);
        break;

    case PROP_HIST_TRIALS: element->hist_trials = g_value_get_int(value); break;

    case PROP_SNAPSHOT_INTERVAL:
        element->snapshot_interval = g_value_get_int(value);
        break;

    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec); break;
    }

    GST_OBJECT_UNLOCK(element);
}

/*
 * get_property()
 */

static void cohfar_accumbackground_get_property(GObject *object,
                                                enum property prop_id,
                                                GValue *value,
                                                GParamSpec *pspec) {
    CohfarAccumbackground *element = COHFAR_ACCUMBACKGROUND(object);

    GST_OBJECT_LOCK(element);

    switch (prop_id) {
    case PROP_IFOS: g_value_set_string(value, element->ifos); break;

    case PROP_HISTORY_FNAME:
        g_value_set_string(value, element->history_fname);
        break;

    case PROP_OUTPUT_NAME:
        g_value_set_string(value, element->output_name);
        break;

    case PROP_OUTPUT_PREFIX:
        g_value_set_string(value, element->output_prefix);
        break;

    case PROP_HIST_TRIALS: g_value_set_int(value, element->hist_trials); break;

    case PROP_SOURCE_TYPE: g_value_set_int(value, element->source_type); break;

    case PROP_SNAPSHOT_INTERVAL:
        g_value_set_int(value, element->snapshot_interval);
        break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec); break;
    }
    GST_OBJECT_UNLOCK(element);
}

/*
 * dispose()
 */

static void cohfar_accumbackground_dispose(GObject *object) {
    CohfarAccumbackground *element = COHFAR_ACCUMBACKGROUND(object);

    if (element->bgstats) {
        // FIXME: free stats
    }
    G_OBJECT_CLASS(parent_class)->dispose(object);
}

/*
 * base_init()
 */

static void cohfar_accumbackground_base_init(gpointer gclass) {
#if 1
    GstElementClass *element_class = GST_ELEMENT_CLASS(gclass);

    gst_element_class_set_details_simple(
      element_class, "Update background xml file given background entries",
      "Background xml updater", "Background xml updater.\n",
      "Qi Chu <qi.chu at ligo dot org>");
    gst_element_class_add_pad_template(
      element_class,
      //		gst_static_pad_template_get(&cohfar_background_src_template)
      gst_pad_template_new("sink", GST_PAD_SINK, GST_PAD_ALWAYS,
                           gst_caps_from_string("application/x-lal-postcoh"))

    );

    gst_element_class_add_pad_template(
      element_class,
      //		gst_static_pad_template_get(&cohfar_background_src_template)
      gst_pad_template_new("src", GST_PAD_SRC, GST_PAD_ALWAYS,
                           gst_caps_from_string("application/x-lal-postcoh")));
#endif
}

/*
 * class_init()
 */

static void
  cohfar_accumbackground_class_init(CohfarAccumbackgroundClass *klass) {
    GObjectClass *gobject_class = G_OBJECT_CLASS(klass);
    ;
    gobject_class->set_property =
      GST_DEBUG_FUNCPTR(cohfar_accumbackground_set_property);
    gobject_class->get_property =
      GST_DEBUG_FUNCPTR(cohfar_accumbackground_get_property);
    gobject_class->dispose = GST_DEBUG_FUNCPTR(cohfar_accumbackground_dispose);

    g_object_class_install_property(
      gobject_class, PROP_IFOS,
      g_param_spec_string("ifos", "ifo names",
                          "ifos that participate in the run", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_HISTORY_FNAME,
      g_param_spec_string("history-fname", "Input history filename",
                          "Reference history background statstics filename",
                          DEFAULT_STATS_FNAME,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_OUTPUT_NAME,
      g_param_spec_string("output-name", "Output filename",
                          "Output background statistics filename",
                          DEFAULT_STATS_FNAME,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_OUTPUT_PREFIX,
      g_param_spec_string("output-prefix", "Output filename prefix",
                          "Output background statistics filename",
                          DEFAULT_STATS_FNAME,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_HIST_TRIALS,
      g_param_spec_int("hist-trials", "number of shifted slides",
                       "Number of shifted slides.", 0, G_MAXINT, 1,
                       G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SOURCE_TYPE,
      g_param_spec_int("source-type", "source type",
                       "(1) BNS, (2) NSBH, or (3) BBH", 1, 3, 1,
                       G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SNAPSHOT_INTERVAL,
      g_param_spec_int(
        "snapshot-interval", "snapshot interval",
        "(-1) never update; (0) snapshot at the end; (N) snapshot background "
        "statistics xml file every N seconds.",
        -1, G_MAXINT, 86400, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
}
/*
 * init()
 */

static void
  cohfar_accumbackground_init(CohfarAccumbackground *element,
                              CohfarAccumbackgroundClass *element_klass) {
    GstElementClass *klass = GST_ELEMENT_CLASS(element_klass);
    element->sinkpad       = gst_pad_new_from_template(
      gst_element_class_get_pad_template(klass, "sink"), "sink");
    gst_element_add_pad(GST_ELEMENT(element), element->sinkpad);

    element->srcpad = gst_pad_new_from_template(
      gst_element_class_get_pad_template(klass, "src"), "src");
    gst_element_add_pad(GST_ELEMENT(element), element->srcpad);

    gst_pad_set_event_function(
      element->sinkpad, GST_DEBUG_FUNCPTR(cohfar_accumbackground_sink_event));

    gst_pad_set_chain_function(element->sinkpad,
                               GST_DEBUG_FUNCPTR(cohfar_accumbackground_chain));

    element->bgstats           = NULL;
    element->zlstats           = NULL;
    element->stats_writer      = NULL;
    element->t_roll_start      = GST_CLOCK_TIME_NONE;
    element->snapshot_interval = NOT_INIT;
}

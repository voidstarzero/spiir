/*
 * Copyright (C) 2015	Qi Chu	<qi.chu@uwa.edu.au>
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
#include <cohfar/cohfar_assignfar.h>
#include <postcohtable.h>
#include <time.h>
#define DEFAULT_STATS_NAME "stats.xml.gz"
/* required minimal background events */
#define MIN_BACKGROUND_NEVENT 1000000
/* make sure the far value to be 0 to indicate no background event yet, or >
 * FLT_MIN */
#define BOUND(a, b) (((b) > 0) ? ((b) < (a) ? (a) : (b)) : 0)
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
#define STATS_FNAME_1W_IDX 0
#define STATS_FNAME_1D_IDX 1
#define STATS_FNAME_2H_IDX 2

#define GST_CAT_DEFAULT cohfar_assignfar_debug
GST_DEBUG_CATEGORY_STATIC(GST_CAT_DEFAULT);

static void additional_initializations(GType type) {
    GST_DEBUG_CATEGORY_INIT(GST_CAT_DEFAULT, "cohfar_assignfar", 0,
                            "cohfar_assignfar element");
}

GST_BOILERPLATE_FULL(CohfarAssignfar,
                     cohfar_assignfar,
                     GstBaseTransform,
                     GST_TYPE_BASE_TRANSFORM,
                     additional_initializations);

enum property {
    PROP_0,
    PROP_IFOS,
    PROP_REFRESH_INTERVAL,
    PROP_SILENT_TIME,
    PROP_INPUT_FNAME
};

static void cohfar_assignfar_set_property(GObject *object,
                                          guint prop_id,
                                          const GValue *value,
                                          GParamSpec *pspec);
static void cohfar_assignfar_get_property(GObject *object,
                                          guint prop_id,
                                          GValue *value,
                                          GParamSpec *pspec);

/* vmethods */
static GstFlowReturn cohfar_assignfar_transform_ip(GstBaseTransform *base,
                                                   GstBuffer *buf);
static void cohfar_assignfar_dispose(GObject *object);

static void update_trigger_fars(PostcohInspiralTable *table,
                                int icombo,
                                CohfarAssignfar *element) {
    TriggerStats *cur_stats = element->bgstats_1w->multistats[icombo];
    int hist_trials         = element->hist_trials;
    double rank_1w, rank_2h, rank_1d;
    double stat = (double)table->cohsnr; // - table->nullsnr
    float far;
    far           = BOUND(FLT_MIN,
                gen_fap_from_feature(stat, (double)table->cmbchisq, cur_stats)
                  * cur_stats->nevent / (cur_stats->livetime * hist_trials));
    table->far_1w = far;
    rank_1w   = trigger_stats_get_val_from_map(stat, (double)table->cmbchisq,
                                             cur_stats->rank->rank_map);
    cur_stats = element->bgstats_1d->multistats[icombo];
    far       = BOUND(FLT_MIN,
                gen_fap_from_feature(stat, (double)table->cmbchisq, cur_stats)
                  * cur_stats->nevent / (cur_stats->livetime * hist_trials));
    table->far_1d = far;
    rank_1d   = trigger_stats_get_val_from_map(stat, (double)table->cmbchisq,
                                             cur_stats->rank->rank_map);
    cur_stats = element->bgstats_2h->multistats[icombo];
    far       = BOUND(FLT_MIN,
                gen_fap_from_feature(stat, (double)table->cmbchisq, cur_stats)
                  * cur_stats->nevent / (cur_stats->livetime * hist_trials));
    table->far_2h = far;
    rank_2h     = trigger_stats_get_val_from_map(stat, (double)table->cmbchisq,
                                             cur_stats->rank->rank_map);
    table->rank = MAX(MAX(rank_1w, rank_1d), rank_2h);

    for (int i = 0; i < MAX_NIFO; ++i) {
        cur_stats = element->bgstats_1w->multistats[i];
        far       = BOUND(
          FLT_MIN, gen_fap_from_feature((double)table->snglsnr[i],
                                        (double)table->chisq[i], cur_stats)
                     * cur_stats->nevent / (cur_stats->livetime * hist_trials));
        table->far_1w_sngl[i] = far;

        cur_stats = element->bgstats_1d->multistats[i];
        far       = BOUND(
          FLT_MIN, gen_fap_from_feature((double)table->snglsnr[i],
                                        (double)table->chisq[i], cur_stats)
                     * cur_stats->nevent / (cur_stats->livetime * hist_trials));
        table->far_1d_sngl[i] = far;

        cur_stats = element->bgstats_2h->multistats[i];
        if (cur_stats->livetime > 0) {
            far                   = BOUND(FLT_MIN,
                        gen_fap_from_feature((double)table->snglsnr[i],
                                             (double)table->chisq[i], cur_stats)
                          * cur_stats->nevent
                          / (cur_stats->livetime * hist_trials));
            table->far_2h_sngl[i] = far;
        }
    }
    GST_DEBUG_OBJECT(
      element, "this long-scale far %f, mid-scale far %f, short-scale far %f",
      table->far_1w, table->far_1d, table->far_2h);
}

/*
 * ============================================================================
 *
 *                     GstBaseTransform Method Overrides
 *
 * ============================================================================
 */

/*
 * transform_ip()
 */

static GstFlowReturn cohfar_assignfar_transform_ip(GstBaseTransform *trans,
                                                   GstBuffer *buf) {
    CohfarAssignfar *element = COHFAR_ASSIGNFAR(trans);
    GstFlowReturn result     = GST_FLOW_OK;

    GstClockTime t_cur = GST_BUFFER_TIMESTAMP(buf);
    if (!GST_CLOCK_TIME_IS_VALID(element->t_start)) element->t_start = t_cur;

    /* Check that we have collected enough backgrounds */
    if (!GST_CLOCK_TIME_IS_VALID(element->t_roll_start)
        && (t_cur - element->t_start) / GST_SECOND
             >= (unsigned)element->silent_time) {
        /* FIXME: the order of input fnames must match the stats order */
        // printf("read input stats to assign far %s, %s, %s\n",
        // element->input_fnames[STATS_FNAME_1W_IDX],
        // element->input_fnames[STATS_FNAME_1D_IDX],
        // element->input_fnames[STATS_FNAME_2H_IDX]);
        element->pass_silent_time = TRUE;
        element->t_roll_start     = t_cur;
        if (!trigger_stats_xml_from_xml(
              element->bgstats_1w, &(element->hist_trials),
              element->input_fnames[STATS_FNAME_1W_IDX])) {
            element->pass_silent_time = FALSE;
            element->t_roll_start     = GST_CLOCK_TIME_NONE;
        }
        if (!trigger_stats_xml_from_xml(
              element->bgstats_1d, &(element->hist_trials),
              element->input_fnames[STATS_FNAME_1D_IDX])) {
            element->pass_silent_time = FALSE;
            element->t_roll_start     = GST_CLOCK_TIME_NONE;
        }
        if (!trigger_stats_xml_from_xml(
              element->bgstats_2h, &(element->hist_trials),
              element->input_fnames[STATS_FNAME_2H_IDX])) {
            element->pass_silent_time = FALSE;
            element->t_roll_start     = GST_CLOCK_TIME_NONE;
        }
    }

    /* Check if it is time to refresh the background stats */
    if (element->pass_silent_time && element->refresh_interval > 0
        && (t_cur - element->t_roll_start) / GST_SECOND
             > (unsigned)element->refresh_interval) {
        element->t_roll_start = t_cur;
        /* FIXME: the order of input fnames must match the stats order */
        // printf("read refreshed stats to assign far.");
        if (!trigger_stats_xml_from_xml(
              element->bgstats_1w, &(element->hist_trials),
              element->input_fnames[STATS_FNAME_1W_IDX])) {
            printf("1w data no longer available\n");
        }

        if (!trigger_stats_xml_from_xml(
              element->bgstats_1d, &(element->hist_trials),
              element->input_fnames[STATS_FNAME_1D_IDX])) {
            printf("1d data no longer available\n");
        }
        if (!trigger_stats_xml_from_xml(
              element->bgstats_2h, &(element->hist_trials),
              element->input_fnames[STATS_FNAME_2H_IDX])) {
            printf("2h data no longer available\n");
        }
    }

    TriggerStats *cur_stats;
    if (element->pass_silent_time) {
        int icombo;
        PostcohInspiralTable *table =
          (PostcohInspiralTable *)GST_BUFFER_DATA(buf);
        PostcohInspiralTable *table_end =
          (PostcohInspiralTable *)(GST_BUFFER_DATA(buf) + GST_BUFFER_SIZE(buf));
        for (; table < table_end; table++) {
            if (table->is_background == FLAG_EMPTY) continue;
            icombo = get_icombo(table->ifos);
            icombo = scan_trigger_ifos(icombo, table);
            if (icombo < 0) {
                fprintf(stderr, "icombo not found, cohfar_assignfar\n");
                exit(0);
            }
            cur_stats = element->bgstats_1w->multistats[element->nifo];
            if (icombo > -1 && cur_stats->nevent > MIN_BACKGROUND_NEVENT) {
                update_trigger_fars(table, element->nifo, element);
            }
        }
    }

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
static gboolean cohfar_assignfar_event(GstBaseTransform *base,
                                       GstEvent *event) {
    CohfarAssignfar *element = COHFAR_ASSIGNFAR(base);

    switch (GST_EVENT_TYPE(event)) {
    case GST_EVENT_EOS:
        //      if (fflush (sink->file))
        //        goto flush_failed;

        GST_LOG_OBJECT(element, "EVENT EOS. Finish assign far");
        break;
    default: break;
    }

    return TRUE;
}

/*
 * set_property()
 */

static void cohfar_assignfar_set_property(GObject *object,
                                          enum property prop_id,
                                          const GValue *value,
                                          GParamSpec *pspec) {
    CohfarAssignfar *element = COHFAR_ASSIGNFAR(object);

    GST_OBJECT_LOCK(element);
    switch (prop_id) {
    case PROP_IFOS:
        element->ifos   = g_value_dup_string(value);
        element->nifo   = strlen(element->ifos) / IFO_LEN;
        element->icombo = get_icombo(element->ifos);
        element->bgstats_1w =
          trigger_stats_xml_create(element->ifos, STATS_XML_TYPE_BACKGROUND);
        element->bgstats_1d =
          trigger_stats_xml_create(element->ifos, STATS_XML_TYPE_BACKGROUND);
        element->bgstats_2h =
          trigger_stats_xml_create(element->ifos, STATS_XML_TYPE_BACKGROUND);
        break;

    case PROP_INPUT_FNAME:
        /* must make sure ifos have been loaded */
        g_assert(element->ifos != NULL);
        element->input_fnames = g_strsplit(g_value_dup_string(value), ",", -1);
        element->ninput       = g_strv_length(element->input_fnames);
        if (element->ninput != 3) {
            fprintf(stderr,
                    "Number of input files for zerolag FAR assignment is not 3 "
                    "but %d,"
                    " your cohfar-assignfar-input-fname option %s might not "
                    "provide the right place"
                    " for the input files. exiting \n",
                    element->ninput, g_value_dup_string(value));
            exit(0);
        }
        break;

    case PROP_SILENT_TIME: element->silent_time = g_value_get_int(value); break;

    case PROP_REFRESH_INTERVAL:
        element->refresh_interval = g_value_get_int(value);
        break;

    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec); break;
    }

    GST_OBJECT_UNLOCK(element);
}

/*
 * get_property()
 */

static void cohfar_assignfar_get_property(GObject *object,
                                          enum property prop_id,
                                          GValue *value,
                                          GParamSpec *pspec) {
    CohfarAssignfar *element = COHFAR_ASSIGNFAR(object);

    GST_OBJECT_LOCK(element);

    switch (prop_id) {
    case PROP_IFOS: g_value_set_string(value, element->ifos); break;

    case PROP_INPUT_FNAME:
        g_value_set_string(value, element->input_fnames);
        break;

    case PROP_SILENT_TIME: g_value_set_int(value, element->silent_time); break;

    case PROP_REFRESH_INTERVAL:
        g_value_set_int(value, element->refresh_interval);
        break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec); break;
    }
    GST_OBJECT_UNLOCK(element);
}

/*
 * dispose()
 */

static void cohfar_assignfar_dispose(GObject *object) {
    CohfarAssignfar *element = COHFAR_ASSIGNFAR(object);

    if (element->bgstats_1w) {
        trigger_stats_xml_destroy(element->bgstats_1w);
        trigger_stats_xml_destroy(element->bgstats_1d);
        trigger_stats_xml_destroy(element->bgstats_2h);
    }
    G_OBJECT_CLASS(parent_class)->dispose(object);
    g_strfreev(element->input_fnames);
}

/*
 * base_init()
 */

static void cohfar_assignfar_base_init(gpointer gclass) {
    GstElementClass *element_class         = GST_ELEMENT_CLASS(gclass);
    GstBaseTransformClass *transform_class = GST_BASE_TRANSFORM_CLASS(gclass);

    gst_element_class_set_details_simple(
      element_class, "assign far to postcoh triggers", "assign far",
      "assign far to postcoh triggers according to a given stats file.\n",
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

    transform_class->transform_ip =
      GST_DEBUG_FUNCPTR(cohfar_assignfar_transform_ip);
    transform_class->event = GST_DEBUG_FUNCPTR(cohfar_assignfar_event);
}

/*
 * class_init()
 */

static void cohfar_assignfar_class_init(CohfarAssignfarClass *klass) {
    GObjectClass *gobject_class = G_OBJECT_CLASS(klass);
    ;
    gobject_class->set_property =
      GST_DEBUG_FUNCPTR(cohfar_assignfar_set_property);
    gobject_class->get_property =
      GST_DEBUG_FUNCPTR(cohfar_assignfar_get_property);
    gobject_class->dispose = GST_DEBUG_FUNCPTR(cohfar_assignfar_dispose);

    g_object_class_install_property(
      gobject_class, PROP_IFOS,
      g_param_spec_string("ifos", "ifo names",
                          "ifos that participate in the pipeline", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_INPUT_FNAME,
      g_param_spec_string("input-fname", "input filename",
                          "Input background statistics filename", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_REFRESH_INTERVAL,
      g_param_spec_int(
        "refresh-interval", "refresh interval",
        "(0) never refresh stats; (N) refresh stats every N seconds. ", 0,
        G_MAXINT, 600, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SILENT_TIME,
      g_param_spec_int("silent-time", "background silent time",
                       "(0) do not need background silent time; (N) allow N "
                       "seconds to accumulate background.",
                       0, G_MAXINT, G_MAXINT,
                       G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
}
/*
 * init()
 */

static void cohfar_assignfar_init(CohfarAssignfar *element,
                                  CohfarAssignfarClass *kclass) {
    element->ifos             = NULL;
    element->bgstats_2h       = NULL;
    element->bgstats_1d       = NULL;
    element->bgstats_1w       = NULL;
    element->input_fnames     = NULL;
    element->t_start          = GST_CLOCK_TIME_NONE;
    element->t_roll_start     = GST_CLOCK_TIME_NONE;
    element->pass_silent_time = FALSE;
    element->ninput           = -1;
}

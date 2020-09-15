/*
 * Copyright (C) 2015 Qi Chu <qi.chu@uwa.edu.au>,
 *               2020 Tom Almeida <tom@tommoa.me>,
 *
 * Permission is hereby granted, free of charge, to any person obtaining a
 * copy of this software and associated documentation files (the
 * "Software"), to deal in the Software without restriction, including
 * without limitation the rights to use, copy, modify, merge, publish,
 * distribute, sublicense, and/or sell copies of the Software, and to
 * permit persons to whom the Software is furnished to do so, subject to
 * the following conditions:
 *
 * The above copyright notice and this permission notice shall be included
 * in all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
 * OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
 * MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
 * IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
 * CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
 * TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
 * SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
 */

#include <cohfar/background_stats_utils.h>
#include <getopt.h>
#include <glib.h>
#include <math.h>
#include <string.h>

//#define __DEBUG__ 0

static void parse_opts(int argc,
                       char *argv[],
                       gchar **pin,
                       gchar **pfmt,
                       gchar **pout,
                       gchar **pifos,
                       gchar **ptype,
                       int *update_pdf) {
    *ptype                    = g_strdup("all");
    *update_pdf               = 0;
    int option_index          = 0;
    struct option long_opts[] = {
        // A comma separated list of files to use for input.
        { "input", required_argument, 0, 'i' },
        // The format of the input files. One of "data" or "stats".
        { "input-format", required_argument, 0, 'f' },
        // The name of the file to output.
        { "output", required_argument, 0, 'o' },
        // The IFOs to use.
        { "ifos", required_argument, 0, 'd' },
        // The type of stat. One of "background", "zerolag", "signal" or "all".
        { "type", required_argument, 0, 'u' },
        // Should we update the PDF?
        { "update-pdf", no_argument, 0, 'p' },
        { 0, 0, 0, 0 }
    };
    int opt;
    while (
      (opt = getopt_long(argc, argv, "i:f:o:d:u:p:", long_opts, &option_index))
      != -1) {
        switch (opt) {
        case 'i': *pin = g_strdup((gchar *)optarg); break;
        case 'f': *pfmt = g_strdup((gchar *)optarg); break;
        case 'o': *pout = g_strdup((gchar *)optarg); break;
        case 'd': *pifos = g_strdup((gchar *)optarg); break;
        case 'u':
            g_free(*ptype);
            *ptype = g_strdup((gchar *)optarg);
            break;
        case 'p': *update_pdf = 1; break;
        default: exit(0);
        }
    }
}

void cohfar_get_data_from_file(gchar **in_fnames,
                               gsl_vector **pdata_dim1,
                               gsl_vector **pdata_dim2) {

    gchar **ifname;
    int nevent = 0;
    char buf[100];
    FILE *fp;
    /* count number of events */
    for (ifname = in_fnames; *ifname; ifname++) {
        if ((fp = fopen(*ifname, "r"))
            == NULL) { /* open file of data in dimension2*/
            printf("read file error dimen1\n");
        }
        while (fgets(buf, 100, fp) != NULL) { // count number of lines in file.
            nevent++;
        }
        fclose(fp);
    }
#ifdef __DEBUG__
    printf("read %d events from file\n", nevent);
#endif
    // data_dim1 and data_dim2 contain the data of two dimensions
    *pdata_dim1           = gsl_vector_alloc(nevent);
    *pdata_dim2           = gsl_vector_alloc(nevent);
    gsl_vector *data_dim1 = *pdata_dim1;
    gsl_vector *data_dim2 = *pdata_dim2;
    int ievent            = 0;
    char *token, *savePtr;
    double idata;
    for (ifname = in_fnames; *ifname; ifname++) {
        if ((fp = fopen(*ifname, "r"))
            == NULL) { /* open file of data in dimension2*/
            printf("read file error dimen1\n");
        }
        while (fgets(buf, 100, fp) != NULL) {
            token = strtok_r(buf, " ", &savePtr);
#ifdef __DEBUG__
            if (ievent == 0) printf("data snr%d, %s\n", ievent, token);
#endif
            sscanf(token, "%lg", &idata);
            gsl_vector_set(data_dim1, ievent, idata);
            token = strtok_r(NULL, " ", &savePtr);
#ifdef __DEBUG__
            if (ievent == 0) printf("data chisq%d, %s\n", ievent, token);
#endif
            sscanf(token, "%lg", &idata);
            gsl_vector_set(data_dim2, ievent, idata);
            ievent++;
        }
        fclose(fp);
    }
    gsl_vector_free(*pdata_dim1);
    gsl_vector_free(*pdata_dim2);
}

void cohfar_get_stats_from_file(gchar **in_fnames,
                                TriggerStatsXML *stats_in,
                                TriggerStatsXML *stats_out,
                                int *hist_trials) {
    gchar **ifname;
    int ifo;
    for (ifname = in_fnames; *ifname; ifname++) {
#ifdef __DEBUG__
        printf("%s\n", *ifname);
#endif
        trigger_stats_xml_from_xml(stats_in, hist_trials, *ifname);
        for (ifo = 0; ifo <= __builtin_popcount(stats_in->icombo + 1); ifo++) {
            trigger_stats_feature_rate_add(stats_out->multistats[ifo]->feature,
                                           stats_in->multistats[ifo]->feature,
                                           stats_out->multistats[ifo]);
            trigger_stats_livetime_add(stats_out->multistats,
                                       stats_in->multistats, ifo);
        }
    }
}

static int get_type(gchar **ptype) {
    if (g_strcmp0(*ptype, "signal") == 0) return STATS_XML_TYPE_SIGNAL;
    if (g_strcmp0(*ptype, "background") == 0) return STATS_XML_TYPE_BACKGROUND;
    if (g_strcmp0(*ptype, "zerolag") == 0) return STATS_XML_TYPE_ZEROLAG;
    if (g_strcmp0(*ptype, "all") == 0) return STATS_XML_TYPE_ALL;
}

static int process_stats_full(
  gchar **in_fnames, int nifo, gchar **pifos, gchar **pout, int *update_pdf) {
    int ifo, hist_trials;
    TriggerStatsXML *zlstats_in =
      trigger_stats_xml_create(*pifos, STATS_XML_TYPE_ZEROLAG);
    TriggerStatsXML *zlstats_out =
      trigger_stats_xml_create(*pifos, STATS_XML_TYPE_ZEROLAG);

    TriggerStatsXML *bgstats_in =
      trigger_stats_xml_create(*pifos, STATS_XML_TYPE_BACKGROUND);
    TriggerStatsXML *bgstats_out =
      trigger_stats_xml_create(*pifos, STATS_XML_TYPE_BACKGROUND);

    TriggerStatsXML *sgstats_in =
      trigger_stats_xml_create(*pifos, STATS_XML_TYPE_SIGNAL);
    TriggerStatsXML *sgstats_out =
      trigger_stats_xml_create(*pifos, STATS_XML_TYPE_SIGNAL);

    cohfar_get_stats_from_file(in_fnames, sgstats_in, sgstats_out,
                               &hist_trials);
    cohfar_get_stats_from_file(in_fnames, zlstats_in, zlstats_out,
                               &hist_trials);
    cohfar_get_stats_from_file(in_fnames, bgstats_in, bgstats_out,
                               &hist_trials);
    if (*update_pdf == 1) {
        for (ifo = 0; ifo < nifo; ifo++) {
            trigger_stats_feature_rate_to_pdf(
              sgstats_out->multistats[ifo]->feature);
            trigger_stats_feature_to_rank(sgstats_out->multistats[ifo]->feature,
                                          sgstats_out->multistats[ifo]->rank);

            trigger_stats_feature_rate_to_pdf(
              zlstats_out->multistats[ifo]->feature);
            trigger_stats_feature_to_rank(zlstats_out->multistats[ifo]->feature,
                                          zlstats_out->multistats[ifo]->rank);

            trigger_stats_feature_rate_to_pdf(
              bgstats_out->multistats[ifo]->feature);
            trigger_stats_feature_to_rank(bgstats_out->multistats[ifo]->feature,
                                          bgstats_out->multistats[ifo]->rank);
        }
    }

    xmlTextWriterPtr stats_writer = NULL;
    GString *tmp_fname            = g_string_new(*pout);
    g_string_append_printf(tmp_fname, "_next");
    trigger_stats_xml_dump(bgstats_out, hist_trials, tmp_fname->str,
                           STATS_XML_WRITE_START, &stats_writer);
    trigger_stats_xml_dump(zlstats_out, hist_trials, tmp_fname->str,
                           STATS_XML_WRITE_MID, &stats_writer);
    trigger_stats_xml_dump(sgstats_out, hist_trials, tmp_fname->str,
                           STATS_XML_WRITE_END, &stats_writer);
#ifdef __DEBUG__
    printf("rename from %s\n", tmp_fname->str);
#endif
    if (g_rename(tmp_fname->str, *pout) != 0) {
        fprintf(stderr, "unable to rename to %s\n", *pout);
        return -1;
    }

    g_string_free(tmp_fname, TRUE);
    trigger_stats_xml_destroy(bgstats_in);
    trigger_stats_xml_destroy(bgstats_out);
    trigger_stats_xml_destroy(zlstats_in);
    trigger_stats_xml_destroy(zlstats_out);
    trigger_stats_xml_destroy(sgstats_in);
    trigger_stats_xml_destroy(sgstats_out);
    return 0;
}

static int process_stats_single(gchar **in_fnames,
                                int nifo,
                                gchar **pifos,
                                gchar **pout,
                                int type,
                                int *update_pdf) {
    int ifo, hist_trials;

    TriggerStatsXML *stats_in  = trigger_stats_xml_create(*pifos, type);
    TriggerStatsXML *stats_out = trigger_stats_xml_create(*pifos, type);
    cohfar_get_stats_from_file(in_fnames, stats_in, stats_out, &hist_trials);
    if (*update_pdf == 1) {
        for (ifo = 0; ifo < nifo; ifo++) {
            trigger_stats_feature_rate_to_pdf(
              stats_out->multistats[ifo]->feature);
            trigger_stats_feature_to_rank(stats_out->multistats[ifo]->feature,
                                          stats_out->multistats[ifo]->rank);
        }
    }
    xmlTextWriterPtr stats_writer = NULL;
    GString *tmp_fname            = g_string_new(*pout);
    g_string_append_printf(tmp_fname, "_next");

    trigger_stats_xml_dump(stats_out, hist_trials, tmp_fname->str,
                           STATS_XML_WRITE_FULL, &stats_writer);
    printf("rename from %s\n", tmp_fname->str);
    if (g_rename(tmp_fname->str, *pout) != 0) {
        fprintf(stderr, "unable to rename to %s\n", *pout);
        return -1;
    }
    g_string_free(tmp_fname, TRUE);
    trigger_stats_xml_destroy(stats_in);
    trigger_stats_xml_destroy(stats_out);
    return 0;
}

int main(int argc, char *argv[]) {
    gchar **pin     = (gchar **)malloc(sizeof(gchar *));
    gchar **pfmt    = (gchar **)malloc(sizeof(gchar *));
    gchar **pout    = (gchar **)malloc(sizeof(gchar *));
    gchar **pifos   = (gchar **)malloc(sizeof(gchar *));
    gchar **ptype   = (gchar **)malloc(sizeof(gchar *));
    int *update_pdf = (int *)malloc(sizeof(int));

    parse_opts(argc, argv, pin, pfmt, pout, pifos, ptype, update_pdf);
    int type = get_type(ptype);
    int nifo = strlen(*pifos) / IFO_LEN;
    int rc; // return value

    gchar **in_fnames =
      g_strsplit(*pin, ",", -1); // split the string completely

    if (g_strcmp0(*pfmt, "data") == 0) {
        gsl_vector *data_dim1 = NULL, *data_dim2 = NULL;
        cohfar_get_data_from_file(in_fnames, &data_dim1, &data_dim2);
        TriggerStatsXML *bgstats_in =
          trigger_stats_xml_create(*pifos, STATS_XML_TYPE_BACKGROUND);
        TriggerStatsXML *bgstats_out =
          trigger_stats_xml_create(*pifos, STATS_XML_TYPE_BACKGROUND);
        // FIXME: hardcoded to only update the last stats
        trigger_stats_feature_rate_update_all(
          data_dim1, data_dim2, bgstats_out->multistats[nifo]->feature,
          bgstats_out->multistats[nifo]);
        trigger_stats_feature_rate_to_pdf(
          bgstats_out->multistats[nifo]->feature);
        trigger_stats_feature_to_rank(bgstats_out->multistats[nifo]->feature,
                                      bgstats_out->multistats[nifo]->rank);
        if (data_dim1) {
            free(data_dim1);
            free(data_dim2);
        }
    } else if (g_strcmp0(*pfmt, "stats") == 0) {
        if (type == STATS_XML_TYPE_ALL) {
            rc = process_stats_full(in_fnames, nifo, pifos, pout, update_pdf);
        } else {
            rc = process_stats_single(in_fnames, nifo, pifos, pout, type,
                                      update_pdf);
        }
    }
    if (rc != 0) return rc;

    g_strfreev(in_fnames);

    g_free(*pin);
    g_free(*pfmt);
    g_free(*pout);
    g_free(*pifos);
    g_free(*ptype);

    free(pin);
    free(pfmt);
    free(pout);
    free(pifos);
    free(ptype);
    free(update_pdf);
    pin   = NULL;
    pfmt  = NULL;
    pout  = NULL;
    pifos = NULL;
    ptype = NULL;
    return 0;
}

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

#ifndef __BACKGROUND_STATS_UTILS_H__
#define __BACKGROUND_STATS_UTILS_H__

#include <LIGOLwHeader.h>
#include <cohfar/background_stats.h>
#include <glib.h>
#include <postcohtable.h>

Bins1D *bins1D_create_long(double cmin, double cmax, int nbin);

Bins2D *bins2D_create(double cmin_x,
                      double cmax_x,
                      int nbin_x,
                      double cmin_y,
                      double cmax_y,
                      int nbin_y);

Bins2D *bins2D_create_long(double cmin_x,
                           double cmax_x,
                           int nbin_x,
                           double cmin_y,
                           double cmax_y,
                           int nbin_y);

TriggerStats **trigger_stats_create(int icombo);

int bins1D_get_idx(double val, Bins1D *bins);

void trigger_stats_feature_rate_update(double snr,
                                       double chisq,
                                       FeatureStats *feature,
                                       TriggerStats *cur_stats);

double trigger_stats_get_val_from_map(double snr, double chisq, Bins2D *bins);

int scan_trigger_ifos(int icombo, PostcohInspiralTable *trigger);

void trigger_stats_livetime_inc(TriggerStats **stats, const int index);

void trigger_stats_xml_reset(TriggerStatsXML *stats);

void signal_stats_init(TriggerStatsXML *sgstats, int source_type);

void trigger_stats_feature_rates_add(FeatureStats *feature1,
                                     FeatureStats *feature2,
                                     TriggerStats *cur_stats);

void trigger_stats_feature_rates_to_pdf(FeatureStats *feature);

double bins2D_get_val(double snr, double chisq, Bins2D *bins);

gboolean trigger_stats_xml_from_xml(TriggerStatsXML *stats,
                                    int *hist_trials,
                                    const char *filename);

gboolean trigger_stats_xml_dump(TriggerStatsXML *stats,
                                int hist_trials,
                                const char *filename,
                                int write_status,
                                xmlTextWriterPtr *pwriter);

TriggerStatsXML *trigger_stats_xml_create(char *ifos, int stats_type);

void trigger_stats_xml_destroy(TriggerStatsXML *stats);

float gen_fap_from_feature(double snr, double chisq, TriggerStats *stats);
#endif /* __BACKGROUND_STATS_UTILS_H__ */

#ifndef __PIPE_MACRO_H__
#define __PIPE_MACRO_H__
/* FIXME: upgrade to include more detectors like KAGRA */
#ifndef IFO_LEN
#define IFO_LEN     2
#define MAX_IFO_LEN 4
#endif

typedef struct _IFOType {
    const char *name;
    int index;
} IFOType;

#define H1_IFO_ID 0
#define L1_IFO_ID 1
#define V1_IFO_ID 2
#define MAX_NIFO 3
#ifdef __cplusplus
constexpr
#endif
  static const IFOType IFOMap[MAX_NIFO] = {
      { "H1", 0 }, // 1 << 0 = 1
      { "L1", 1 }, // 1 << 1 = 2
      { "V1", 2 }, // 1 << 2 = 4
  };
#define MAX_IFO_COMBOS 7 // 2^3-1
// A combination is sum(1 << index) - 1
// This gives us some nice mathematical properties that we can use to check
// if an IFO exists in a given ComboMap
static const IFOType IFOComboMap[MAX_IFO_COMBOS] = {
    { "H1", 0 },   { "L1", 1 },   { "H1L1", 2 },   { "V1", 3 },
    { "H1V1", 4 }, { "L1V1", 5 }, { "H1L1V1", 6 },
};
/* function given a random ifo, output the index in the IFOComboMap list,
 * implemented in background_stats_utils.c */
int get_icombo(char *ifos);

#ifndef MAX_ALLIFO_LEN
#define MAX_ALLIFO_LEN 14
#endif

#define MAX_SKYMAP_FNAME_LEN 50
#define FLAG_FOREGROUND      0
#define FLAG_BACKGROUND      1
#define FLAG_EMPTY           2

/* definition of array for background statistics */
#define LOGSNR_CMIN   0.54 // center of the first bin
#define LOGSNR_CMAX   3.0 // center of the last bin
#define LOGSNR_NBIN   300 // step is 0.01
#define LOGCHISQ_CMIN -1.2
#define LOGCHISQ_CMAX 3.5
#define LOGCHISQ_NBIN 300

#define LOGRANK_CMIN                                                           \
    -30 // 10^-30, minimum cdf, extrapolating if less than this min
#define LOGRANK_CMAX 0 //
#define LOGRANK_NBIN 300 // FIXME: enough for accuracy

/* array names in xml files */
#define BACKGROUND_XML_FEATURE_NAME "background_feature"
#define SNR_RATE_SUFFIX             "lgsnr_rate"
#define CHISQ_RATE_SUFFIX           "lgchisq_rate"
#define SNR_CHISQ_RATE_SUFFIX       "lgsnr_lgchisq_rate"
#define SNR_CHISQ_PDF_SUFFIX        "lgsnr_lgchisq_pdf"
#define BACKGROUND_XML_RANK_NAME    "background_rank"
#define RANK_MAP_SUFFIX             "rank_map"
#define RANK_RATE_SUFFIX            "rank_rate"
#define RANK_PDF_SUFFIX             "rank_pdf"
#define RANK_FAP_SUFFIX             "rank_fap"

#define ZEROLAG_XML_FEATURE_NAME "zerolag_feature"
#define ZEROLAG_XML_RANK_NAME    "zerolag_rank"
#define SIGNAL_XML_FEATURE_NAME  "signal_feature"
#define SIGNAL_XML_RANK_NAME     "signal_rank"

#define STATS_XML_ID_NAME         "gstlal_postcohspiir_stats"
#define STATS_XML_TYPE_BACKGROUND 1
#define STATS_XML_TYPE_ZEROLAG    2
#define STATS_XML_TYPE_SIGNAL     3
#define STATS_XML_TYPE_ALL        4

#define STATS_XML_WRITE_START 1
#define STATS_XML_WRITE_MID   2
#define STATS_XML_WRITE_END   3
#define STATS_XML_WRITE_FULL  4

#define PNOISE_MIN_LIMIT -30
#define PSIG_MIN_LIMIT   -30
#define LR_MIN_LIMIT     -30
#define SOURCE_TYPE_BNS  1

#define DETRSP_XML_ID_NAME "gstlal_postcoh_detrsp_map"
#endif

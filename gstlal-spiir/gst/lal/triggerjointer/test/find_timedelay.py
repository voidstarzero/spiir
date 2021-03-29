# finding maximum light travel time of the target detector and other detectors
import lal

# see the lalCachedDetectors for detector index
test_ifo_names = ['H1','V1', 'K1']
test_ifos = [
    lal.CachedDetectors[lal.LHO_4K_DETECTOR],
    lal.CachedDetectors[lal.VIRGO_DETECTOR],
    lal.CachedDetectors[lal.KAGRA_DETECTOR]
]

exist_ifos = [
    lal.CachedDetectors[lal.LHO_4K_DETECTOR],
    lal.CachedDetectors[lal.LLO_4K_DETECTOR]
]

max_lt = 0
for i, this_test_ifo in enumerate(test_ifos):
    for j, this_exist_ifo in enumerate(exist_ifos):
        this_lt = lal.LightTravelTime(this_test_ifo, this_exist_ifo)
        if this_lt > max_lt:
            max_lt = this_lt
            max_exist_ifo = this_exist_ifo
    print("IFO {}, max light travel time {} to IFO {} in nano seconds".format(
        test_ifo_names[i], max_lt, max_exist_ifo.frDetector.name))

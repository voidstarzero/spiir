#include <lal/Detectors.h>
#include <lal/DetResponse.h>
#include <lal/TimeDelay.h>
#include <lalsimulation/lalsimulation.h>

#include <chealpix.h>

void create_coherent_skymap(unsigned char order) {
	unsigned long nside = (unsigned long) 1 << order;
	unsigned long npix = nside2npix(nside);
}

/* get u matirx for each sky direction from detector response for each sky 
 * direction at every minute
 */
LALDector *detectors create_detectors_from_name(
		char **detectors_name,
		int num_detectors,
		)
{
	LALDetector *detectors = (LALDetector *)malloc(sizeof(LALDector) * num_detectors);
	for (i=0; i<num_detectors; i++) 
		detectors[i] = XLALDetectorPrefixToLALDetector(detectors_name[i]);
	return detectors;
}

void create_det_response_skymap(
		LALDector *detectors,
		)
{

	for (gmst; gmst<gmst_end; gmst+=gsmt_step) {
		for (sky_index; sky_index<sky_index_end; sky_index++) {
			for (i=0; i<num_detectors; i++) {
	
		
			/* get fplus, fcross from lalsuite DetResponse.c */

			XLALComputeDetAMResponse(*fplus, *fcross, detectors[i].response, ra, dec, psi, gmst);
		
			A_matrix[i][1] = *fplus;
			A_matrix[i][2] = *fcross;
			}
				
			u_matrix = svd(A_matrix);
			u_map[gmst_index][sky_index] = u_matrix;

		}
	}
}

void create_time_delay_skymap(
		LALDetector * detectors,
		)
{
	XLALArrivalTimeDiff(detectors[i].location, detectors[j].location, ra, dec, gpstime);

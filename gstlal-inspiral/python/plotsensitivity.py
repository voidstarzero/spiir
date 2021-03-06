# Copyright (C) 2015 Chad Hanna, Cody Messick
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
# Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

import datetime
from itertools import chain
import math
import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams.update({
        "font.size": 10.0,
        "axes.titlesize": 10.0,
        "axes.labelsize": 10.0,
        "xtick.labelsize": 8.0,
        "ytick.labelsize": 8.0,
        "legend.fontsize": 8.0,
        "figure.dpi": 200,
        "savefig.dpi": 200,
        "text.usetex": True
})
from matplotlib import pyplot as plt
from matplotlib import ticker as tkr
import numpy

from glue.ligolw import utils as ligolw_utils
from gstlal import plotsegments
from lal import GPSToUTC
from pylal import imr_utils
from pylal import rate

# FIXME Currently this program assumes there are only two ifos
def parse_sensitivity_docs(database, cumulative_segments_file, simdb_query_end_time):
	# Get segments and coincident segment bins
	seglistdicts = plotsegments.parse_segments_xml(cumulative_segments_file)
	# FIXME This will need to be generalized to more than two IFOs
	seg_bins = [ligotimegps.seconds for ligotimegps in sorted(list(chain.from_iterable(seglistdicts["joint segments"]["H1L1"])))]
	# Adjust the last segment bin so that we dont get a situation where we
	# have segment information for an interval of time that we haven't
	# queries simdb for yet (this is necessary due to the asynchronous
        # nature of the cumulative_segments.xml downloads and the simdb
        # queries)
	seg_bins[-1] = min(seg_bins[-1], simdb_query_end_time)

	db = imr_utils.DataBaseSummary([database], live_time_program = "gstlal_inspiral", veto_segments_name = None, data_segments_name = "statevectorsegments", tmp_path = None, verbose = True)

	found_inj = db.found_injections_by_instrument_set[frozenset(("H1","L1"))]
	missed_inj = db.missed_injections_by_instrument_set[frozenset(("H1","L1"))]

	# Constrain found to be events with a far <= 3.86e-7, roughly once per
	# 30 days, and sort found injections by time
	missed_inj.extend([f[1] for f in found_inj if f[0] > 3.86e-7])
	found_inj = sorted([f for f in found_inj if f[0] <= 3.86e-7], key=lambda f: f[1].geocent_end_time)

	# Throw away any events that have been downloaded before their segment
	# information was updated (this is caused by the asynchronous nature of
	# the cumulative_segments.xml downloads and the simdb queries)
	found_inj = [f for f in found_inj if f[1].geocent_end_time < seg_bins[-1]]
	missed_inj = [m for m in missed_inj if m.geocent_end_time < seg_bins[-1]]

	return found_inj, missed_inj, seglistdicts, seg_bins

def plot_range(found_inj, missed_inj, seg_bins, (tlo, thi), (dlo, dhi), horizon_history, colors = {'H1': numpy.array((1.0, 0.0, 0.0)), 'L1':  numpy.array((0.0, 0.8, 0.0)), 'V1':  numpy.array((1.0, 0.0, 1.0))}, fig = None, axes = None):
	if fig is None:
		fig = plt.figure()
	if axes is None:
		axes = fig.add_subplot(111)
	
	# FIXME Add number of distance bins as option
	ndbins = rate.NDBins((rate.LinearBins(dlo, dhi, int(dhi - dlo + 1)), rate.IrregularBins(seg_bins)))
	vol, err = imr_utils.compute_search_volume_in_bins([f[1] for f in found_inj], missed_inj + [f[1] for f in found_inj], ndbins, lambda sim: (sim.distance, sim.geocent_end_time))

	x = vol.bins[0].lower()
	dx = vol.bins[0].upper() - vol.bins[0].lower()
	y = (vol.array * 3./ (4. * math.pi))**(1./3.)
	yerr = (1./3.) * (3./(4.*math.pi))**(1./3.)*vol.array**(-2./3.) * err.array
	yerr[~numpy.isfinite(yerr)] = 0.
	err_lo = y - 2.0 * yerr
	err_lo[err_lo<=0] = 0.
	err_hi = y + 2.0 * yerr

	axes.bar(x, err_hi-err_lo, bottom=err_lo, color='c', alpha=0.6, label='95\% confidence interval\non range estimated \nfrom injections', width=dx, linewidth=0)
	for ifo in horizon_history.keys():
		horizon_times = numpy.array(horizon_history[ifo].keys()).clip(tlo,thi)
		sensemon_range = numpy.array([horizon_history[ifo][seg]/2.26 for seg in horizon_times])
		axes.scatter(horizon_times.compress(horizon_times <= horizon_history[ifo].maxkey()), sensemon_range, s = 1, color = colors[ifo], label='%s SenseMon Range' % ifo, alpha=1.0)


	xticks = numpy.linspace(tlo,thi,9)
	x_format = tkr.FuncFormatter(lambda x, pos: datetime.datetime(*GPSToUTC(int(x))[:7]).strftime("%Y-%m-%d, %H:%M:%S UTC"))
	axes.set_ylabel('Range (Mpc)')
	axes.set_xlim(tlo,thi)
	axes.set_ylim(0,5*(int(max(err_hi)/5.)+1))
	axes.xaxis.set_major_formatter(x_format)
	axes.xaxis.set_ticks(xticks)
	axes.grid(color=(0.1,0.4,0.5), linewidth=2)
	return fig, axes

def plot_missedfound(found_inj, missed_inj, (tlo, thi), fig = None, axes = None):
	if fig is None:
		fig = plt.figure()
	if axes is None:
		axes = fig.add_subplot(111)
	xticks = numpy.linspace(tlo,thi,9)
	x_format = tkr.FuncFormatter(lambda x, pos: datetime.datetime(*GPSToUTC(int(x))[:7]).strftime("%Y-%m-%d, %H:%M:%S UTC"))
	axes.grid(color=(0.1,0.4,0.5), linewidth=2)
	axes.semilogy([float(m.get_time_geocent()) for m in missed_inj], [m.eff_dist_l for m in missed_inj], '.k', label=r'Missed (FAR$>$1 / month)')
	axes.semilogy([float(f[1].get_time_geocent()) for f in found_inj], [f[1].eff_dist_l for f in found_inj], '.b', label=r'Found (FAR$\leq$1 / month)', alpha=0.75)
	axes.set_ylabel(r'D$_{\mathrm{eff}_{\mathrm{H1}}}$ (Mpc)')
	axes.xaxis.set_major_formatter(x_format)
	axes.set_xlim(tlo,thi)
	axes.xaxis.set_ticks(xticks)
	return fig, axes

def plot_missedfound_range_segments(found_inj, missed_inj, seglistdicts, seg_bins, (tlo, thi), (dlo, dhi), horizon_history, colors = {'H1': numpy.array((1.0, 0.0, 0.0)), 'L1':  numpy.array((0.0, 0.8, 0.0)), 'V1':  numpy.array((1.0, 0.0, 1.0)), 'H1L1': numpy.array((.5, .5, .5))}, fig = None):
	# FIXME In order to save the figure with the legends included off to
	# the right, users need to do something along these lines:
	# fig.savefig(path, additional_artists=[lgd1, lgd2], bbox_inches=matplotlib.transforms.Bbox([[0.,0.],[fig_width+1.5,fig_height]]))
	# There has to be a better a way, and I think tight_layout() may do it
	# automatically

	if fig is None:
		fig = plt.figure()
	fig, ax1 = plot_missedfound(found_inj, missed_inj, (tlo, thi), fig = fig, axes = fig.add_subplot(311))
	ax1.xaxis.set_tick_params(labeltop='on', labelbottom='off')

	# Move the legend out of the plot
	lgd1 = ax1.legend(numpoints = 1, loc=6, bbox_to_anchor=(1.01,0.5))
	plt.setp(ax1.get_xticklabels(), rotation = 10.)

	fig, ax2 = plot_range(found_inj, missed_inj, seg_bins, (tlo, thi), (dlo, dhi), horizon_history, colors = colors, fig = fig, axes = fig.add_subplot(312))
	ax2.xaxis.set_ticklabels([])
	# Move the legend out of the plot
	lgd2 = ax2.legend(loc=6, bbox_to_anchor=(1.01,0.5))

	fig, ax3 = plotsegments.plot_segments_history(seglistdicts, segments_to_plot = ["state vector", "joint segments"], t_max = thi, length = (thi - tlo), fig = fig, axes = fig.add_subplot(313))
	plt.setp(ax3.get_xticklabels(), rotation = 10.)

	return fig, lgd1, lgd2

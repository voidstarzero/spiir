# Copyright (C) 2011  Kipp Cannon, Chad Hanna, Drew Keppel
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


#
# =============================================================================
#
#                                   Preamble
#
# =============================================================================
#


from glue import iterutils
from glue import segments
from glue.ligolw import lsctables
from pylal import ligolw_thinca
from pylal.xlal.datatypes.ligotimegps import LIGOTimeGPS
lsctables.LIGOTimeGPS = LIGOTimeGPS


#
# =============================================================================
#
#                              Pipeline Elements
#
# =============================================================================
#


#
# sngl_inspiral<-->sngl_inspiral comparison function
#


def event_comparefunc(event_a, offset_a, event_b, offset_b, light_travel_time, delta_t):
	return (event_a.mass1 != event_b.mass1) or (event_a.mass2 != event_b.mass2) or (event_a.chi != event_b.chi) or (float(abs(event_a.get_end() + offset_a - event_b.get_end() - offset_b)) > light_travel_time + delta_t)


#
# allowed instrument combinations (yes, hard-coded, just take off, eh)
#


allowed_instrument_combos = (frozenset(("H1", "H2", "L1")), frozenset(("H1", "L1", "V1")), frozenset(("H1", "L1")), frozenset(("H1", "V1")), frozenset(("L1", "V1")))


#
# gstlal_inspiral's triggers cause a divide-by-zero error in the effective
# SNR method attached to the triggers, so we replace it with one that works
# for the duration of the ligolw_thinca() call.  this is the function with
# which we replace it
#


def get_effective_snr(self, fac):
	return self.snr


#
# on-the-fly thinca implementation
#


class StreamThinca(object):
	def __init__(self, xmldoc, process_id, coincidence_threshold, thinca_interval = 50.0):
		self.xmldoc = xmldoc
		self.process_id = process_id
		# can't use table.new_from_template() because we need to
		# ensure we have a Table subclass, not a DBTable subclass
		self.sngl_inspiral_table = lsctables.New(lsctables.SnglInspiralTable, lsctables.table.get_table(self.xmldoc, lsctables.SnglInspiralTable.tableName).columnnames)
		self.coinc_event_map_table = lsctables.New(lsctables.CoincMapTable)
		self.last_boundary = -segments.infinity()
		# when using the normal coincidence function from
		# ligolw_thinca this is the e-thinca parameter.  when using
		# a \Delta t only coincidence test it's the \Delta t window
		# not including the light travel time
		self.coincidence_threshold = coincidence_threshold
		# stay this far away from the boundaries of the available
		# triggers
		self.coincidence_back_off = max(abs(offset) for offset in lsctables.table.get_table(self.xmldoc, lsctables.TimeSlideTable.tableName).getColumnByName("offset"))
		self.thinca_interval = thinca_interval
		# set of the event ids of triggers currently in ram that
		# have already been used in coincidences
		self.ids = set()
		# sngls that are not involved in coincidences
		self.noncoinc_sngls = []


	def run_coincidence(self, boundary):
		# wait until we've accumulated thinca_interval seconds
		if self.last_boundary + self.thinca_interval > boundary - self.coincidence_back_off:
			return

		# remove triggers that are too old to be useful.  save any
		# that were never used in coincidences
		discard_boundary = self.last_boundary - self.coincidence_back_off
		self.noncoinc_sngls[:] = (row for row in self.sngl_inspiral_table if row.get_end() < discard_boundary and row.event_id not in self.ids)
		iterutils.inplace_filter(lambda row: row.get_end() >= discard_boundary, self.sngl_inspiral_table)

		# replace tables with our versions
		orig_sngl_inspiral_table = lsctables.table.get_table(self.xmldoc, lsctables.SnglInspiralTable.tableName)
		self.xmldoc.childNodes[-1].replaceChild(self.sngl_inspiral_table, orig_sngl_inspiral_table)
		orig_coinc_event_map_table = lsctables.table.get_table(self.xmldoc, lsctables.CoincMapTable.tableName)
		self.xmldoc.childNodes[-1].replaceChild(self.coinc_event_map_table, orig_coinc_event_map_table)

		# define once-off ntuple_comparefunc() so we can pass the
		# coincidence segment in as a default value for the seg
		# keyword argument
		def ntuple_comparefunc(events, offset_vector, seg = segments.segment(self.last_boundary, boundary)):
			return frozenset(event.ifo for event in events) not in allowed_instrument_combos or ligolw_thinca.coinc_inspiral_end_time(events, offset_vector) not in seg

		# swap .get_effective_snr() method on trigger class
		orig_get_effective_snr, ligolw_thinca.SnglInspiral.get_effective_snr = ligolw_thinca.SnglInspiral.get_effective_snr, get_effective_snr

		# find coincs
		ligolw_thinca.ligolw_thinca(
			self.xmldoc,
			process_id = self.process_id,
			EventListType = ligolw_thinca.InspiralEventList,
			CoincTables = ligolw_thinca.InspiralCoincTables,
			coinc_definer_row = ligolw_thinca.InspiralCoincDef,
			event_comparefunc = event_comparefunc,
			thresholds = self.coincidence_threshold,
			ntuple_comparefunc = ntuple_comparefunc
		)

		# restore .get_effective_snr() method on trigger class
		ligolw_thinca.SnglInspiral.get_effective_snr = orig_get_effective_snr

		# put the original table objects back
		self.xmldoc.childNodes[-1].replaceChild(orig_sngl_inspiral_table, self.sngl_inspiral_table)
		self.xmldoc.childNodes[-1].replaceChild(orig_coinc_event_map_table, self.coinc_event_map_table)

		# record boundary
		self.last_boundary = boundary


	def move_results_to_output(self):
		"""
		Copy rows into real output document.  FOR INTERNAL USE ONLY.
		"""
		if self.coinc_event_map_table:
			# retrieve the target tables
			real_coinc_event_map_table = lsctables.table.get_table(self.xmldoc, lsctables.CoincMapTable.tableName)
			real_sngl_inspiral_table = lsctables.table.get_table(self.xmldoc, lsctables.SnglInspiralTable.tableName)

			# figure out the IDs of triggers that have been
			# used in coincs for the first time, and update the
			# set of IDs of all triggers that have been used in
			# coincs
			index = dict((row.event_id, row) for row in self.sngl_inspiral_table)
			self.ids &= set(index)
			newids = set(self.coinc_event_map_table.getColumnByName("event_id")) - self.ids
			self.ids |= newids

			# move/copy rows into target tables
			self.coinc_event_map_table.reverse()	# so the loop that follows preserves order
			while self.coinc_event_map_table:
				real_coinc_event_map_table.append(self.coinc_event_map_table.pop())
			for id in newids:
				real_sngl_inspiral_table.append(index[id])


	def add_events(self, events, boundary):
		# convert the new row objects to the type required by
		# ligolw_thinca(), and append to our sngl_inspiral table
		for old_event in events:
			new_event = ligolw_thinca.SnglInspiral()
			for col in self.sngl_inspiral_table.columnnames:
				setattr(new_event, col, getattr(old_event, col))
			self.sngl_inspiral_table.append(new_event)

		# clear the list of non-coincident triggers
		del self.noncoinc_sngls[:]

		# anything to do?
		if self.sngl_inspiral_table:
			# coincidence.  since the triggers are appended to
			# the table, we can rely on the last one to provide
			# an estimate of the most recent time stamps to
			# come out of the pipeline
			self.run_coincidence(boundary)

			# copy triggers into real output document
			self.move_results_to_output()


	def flush(self):
		# clear the list of non-coincident triggers
		del self.noncoinc_sngls[:]

		# coincidence
		self.run_coincidence(segments.infinity())

		# copy triggers into real output document
		self.move_results_to_output()

		# save all remaining triggers that weren't used in coincs
		self.noncoinc_sngls.extend(row for row in self.sngl_inspiral_table if row.event_id not in self.ids)
		del self.sngl_inspiral_table[:]

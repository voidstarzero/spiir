ADD_CFLAGS = $(GSTLAL_SPIIR_CFLAGS) $(LAPACKE_CFLAGS) $(SPIIR_FIX_COMPLEX_CFLAGS)
ADD_LIBS= $(top_srcdir)/lib/libgstlalspiir.la

plugin_LTLIBRARIES = libgstlalspiir.la

libgstlalspiir_la_SOURCES = \
	gstlalspiir.c \
	triggerjointer/triggerjointer.c
libgstlalspiir_la_CFLAGS = $(ADD_CFLAGS) $(GSL_CFLAGS) $(LAL_CFLAGS) $(GSTLAL_CFLAGS) $(gstreamer_CFLAGS)
libgstlalspiir_la_LIBADD = $(ADD_LIBS)
libgstlalspiir_la_LDFLAGS = $(AM_LDFLAGS) $(GSL_LIBS) $(LAL_LIBS) $(GSTLAL_LIBS) $(gstreamer_LIBS) $(GSTLAL_PLUGIN_LDFLAGS) 

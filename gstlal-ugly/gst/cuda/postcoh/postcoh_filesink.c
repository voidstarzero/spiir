/* 
 * Copyright (C) 2014 Qi Chu <qi.chu@ligo.org>
 * most of this file is copied from gstfilesink.c
 *
 * Copyright (C) 1999,2000 Erik Walthinsen <omega@cse.ogi.edu>
 *                    2000 Wim Taymans <wtay@chello.be>
 *                    2006 Wim Taymans <wim@fluendo.com>
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Library General Public
 * License as published by the Free Software Foundation; either
 * version 2 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Library General Public License for more details.
 *
 * You should have received a copy of the GNU Library General Public
 * License along with this library; if not, write to the
 * Free Software Foundation, Inc., 59 Temple Place - Suite 330,
 * Boston, MA 02111-1307, USA.
 */

/* This element will synchronize the snr sequencies from all detectors, find 
 * peaks from all detectors and for each peak, do null stream analysis.
 */


#include <gst/gst.h>
#include <errno.h>

#include <string.h>
#include <math.h>
#include "postcoh_filesink.h"
#include "postcoh_table_utils.h"

enum 
{
	PROP_0,
	PROP_LOCATION,
	PROP_COMPRESS
};

/* The following content is mostly copied from gstfilesink.c */

#if 0
/* Copy of glib's g_fopen due to win32 libc/cross-DLL brokenness: we can't
 * use the 'file pointer' opened in glib (and returned from this function)
 * in this library, as they may have unrelated C runtimes. */
static FILE *
gst_fopen (const gchar * filename, const gchar * mode)
{
#ifdef G_OS_WIN32
  wchar_t *wfilename = g_utf8_to_utf16 (filename, -1, NULL, NULL, NULL);
  wchar_t *wmode;
  FILE *retval;
  int save_errno;

  if (wfilename == NULL) {
    errno = EINVAL;
    return NULL;
  }

  wmode = g_utf8_to_utf16 (mode, -1, NULL, NULL, NULL);

  if (wmode == NULL) {
    g_free (wfilename);
    errno = EINVAL;
    return NULL;
  }

  retval = _wfopen (wfilename, wmode);
  save_errno = errno;

  g_free (wfilename);
  g_free (wmode);

  errno = save_errno;
  return retval;
#else
  return fopen (filename, mode);
#endif
}
#endif

static void postcoh_filesink_dispose (GObject * object);

static void postcoh_filesink_set_property (GObject * object, guint prop_id,
    const GValue * value, GParamSpec * pspec);
static void postcoh_filesink_get_property (GObject * object, guint prop_id,
    GValue * value, GParamSpec * pspec);

//static gboolean postcoh_filesink_open_file (PostcohFilesink * sink);
//static void postcoh_filesink_close_file (PostcohFilesink * sink);

static gboolean postcoh_filesink_start (GstBaseSink * sink);
static gboolean postcoh_filesink_stop (GstBaseSink * sink);
static gboolean postcoh_filesink_event (GstBaseSink * sink, GstEvent * event);
static GstFlowReturn postcoh_filesink_render (GstBaseSink * sink,
    GstBuffer * buffer);

//static gboolean postcoh_filesink_do_seek (PostcohFilesink * filesink,
//    guint64 new_offset);
//static gboolean postcoh_filesink_get_current_offset (PostcohFilesink * filesink,
//    guint64 * p_pos);

//static gboolean postcoh_filesink_query (GstBaseSink * bsink, GstQuery * query);

static void postcoh_filesink_uri_handler_init (gpointer g_iface,
    gpointer iface_data);

//#define postcoh_filesink_parent_class parent_class

#define GST_CAT_DEFAULT postcoh_filesink_debug
GST_DEBUG_CATEGORY_STATIC(GST_CAT_DEFAULT);

static void
_do_init (GType filesink_type)
{
  static const GInterfaceInfo urihandler_info = {
    postcoh_filesink_uri_handler_init,
    NULL,
    NULL
  };

  g_type_add_interface_static (filesink_type, GST_TYPE_URI_HANDLER,
      &urihandler_info);
  GST_DEBUG_CATEGORY_INIT (GST_CAT_DEFAULT, "postcoh filesink", 0,
      "postcoh filesink element");
}

GST_BOILERPLATE_FULL (
		PostcohFilesink, 
		postcoh_filesink, 
		GstBaseSink,
    		GST_TYPE_BASE_SINK, 
		_do_init);

static void
postcoh_filesink_base_init(gpointer g_class)
{

  GstElementClass *gstelement_class = GST_ELEMENT_CLASS (g_class);
  gst_element_class_set_details_simple (gstelement_class,
      "Postcoh File Sink",
      "Sink/File", "Write postcoh tables to a xml file",
      "Qi Chu <qi.chu at ligo dot org>");

  gst_element_class_add_pad_template (
		  gstelement_class,
		  gst_pad_template_new(
			  "sink",
			  GST_PAD_SINK,
			  GST_PAD_ALWAYS,
			  gst_caps_from_string(
				  "application/x-lal-postcoh"
			  )
			  )
		  );

}

static void
postcoh_filesink_class_init (PostcohFilesinkClass * klass)
{
  GObjectClass *gobject_class = G_OBJECT_CLASS (klass);
  GstBaseSinkClass *gstbasesink_class = GST_BASE_SINK_CLASS (klass);

  gobject_class->dispose = postcoh_filesink_dispose;

  gobject_class->set_property = postcoh_filesink_set_property;
  gobject_class->get_property = postcoh_filesink_get_property;

  g_object_class_install_property (gobject_class, PROP_LOCATION,
      g_param_spec_string ("location", "File Location",
          "Location of the file to write", NULL,
          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

  g_object_class_install_property (gobject_class, PROP_COMPRESS,
      g_param_spec_int ("compression", "if compress",
          "Compression (1), Non-compression (0)", 0, 1, 1,
          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));


  
  gstbasesink_class->start = GST_DEBUG_FUNCPTR (postcoh_filesink_start);
  gstbasesink_class->stop = GST_DEBUG_FUNCPTR (postcoh_filesink_stop);
//  gstbasesink_class->query = GST_DEBUG_FUNCPTR (postcoh_filesink_query);
  gstbasesink_class->render = GST_DEBUG_FUNCPTR (postcoh_filesink_render);
  gstbasesink_class->event = GST_DEBUG_FUNCPTR (postcoh_filesink_event);

}

static void
postcoh_filesink_init (PostcohFilesink * filesink, PostcohFilesinkClass *klass)
{
  filesink->filename = NULL;
  filesink->file = NULL;
  filesink->uri = NULL;

  gst_base_sink_set_sync (GST_BASE_SINK (filesink), FALSE);
}

static void
postcoh_filesink_dispose (GObject * object)
{
  PostcohFilesink *sink = POSTCOH_FILESINK (object);

  G_OBJECT_CLASS (parent_class)->dispose (object);

  if (sink->uri) {
	  g_free (sink->uri);
	  sink->uri = NULL;
  }
  if (sink->filename) {
	  g_free (sink->filename);
	  sink->filename = NULL;
  }
}

static gboolean
postcoh_filesink_set_location (PostcohFilesink * sink, const gchar * location)
{
  if (sink->file)
    goto was_open;

  g_free (sink->filename);
  g_free (sink->uri);
  if (location != NULL) {
    /* we store the filename as we received it from the application. On Windows
     * this should be in UTF8 */
    sink->filename = g_strdup (location);
    sink->uri = gst_filename_to_uri (location, NULL);
    GST_INFO ("filename : %s", sink->filename);
    GST_INFO ("uri      : %s", sink->uri);
  } else {
    sink->filename = NULL;
    sink->uri = NULL;
  }

  return TRUE;

  /* ERRORS */
was_open:
  {
    g_warning ("Changing the `location' property on filesink when a file is "
        "open is not supported.");
    return FALSE;
  }
}

static void
postcoh_filesink_set_property (GObject * object, guint prop_id,
    const GValue * value, GParamSpec * pspec)
{
  PostcohFilesink *sink = POSTCOH_FILESINK (object);

  switch (prop_id) {
    case PROP_LOCATION:
      postcoh_filesink_set_location (sink, g_value_get_string (value));
      break;
    case PROP_COMPRESS:
      sink->compress = g_value_get_int(value);
      break;
    default:
      G_OBJECT_WARN_INVALID_PROPERTY_ID (object, prop_id, pspec);
      break;
  }
}

static void
postcoh_filesink_get_property (GObject * object, guint prop_id, GValue * value,
    GParamSpec * pspec)
{
  PostcohFilesink *sink = POSTCOH_FILESINK (object);

  switch (prop_id) {
    case PROP_LOCATION:
      g_value_set_string (value, sink->filename);
      break;
    case PROP_COMPRESS:
      g_value_set_int (value, sink->compress);
      break;
    default:
      G_OBJECT_WARN_INVALID_PROPERTY_ID (object, prop_id, pspec);
      break;
  }
}
#if 0
static gboolean
postcoh_filesink_open_file (PostcohFilesink * sink)
{
  gint mode;

  /* open the file */
  if (sink->filename == NULL || sink->filename[0] == '\0')
    goto no_filename;

  sink->file = gst_fopen (sink->filename, "wb");
  if (sink->file == NULL)
    goto open_failed;

  return TRUE;

  /* ERRORS */
no_filename:
  {
    GST_ELEMENT_ERROR (sink, RESOURCE, NOT_FOUND,
        (_("No file name specified for writing.")), (NULL));
    return FALSE;
  }
open_failed:
  {
    GST_ELEMENT_ERROR (sink, RESOURCE, OPEN_WRITE,
        (_("Could not open file \"%s\" for writing."), sink->filename),
        GST_ERROR_SYSTEM);
    return FALSE;
  }
}

static void
postcoh_filesink_close_file (PostcohFilesink * sink)
{
  if (sink->file) {
    if (fclose (sink->file) != 0)
      goto close_failed;

    GST_DEBUG_OBJECT (sink, "closed file");
    sink->file = NULL;

  }
  return;

  /* ERRORS */
close_failed:
  {
    GST_ELEMENT_ERROR (sink, RESOURCE, CLOSE,
        (_("Error closing file \"%s\"."), sink->filename), GST_ERROR_SYSTEM);
    return;
  }
}
#endif
/* handle events (search) */
static gboolean
postcoh_filesink_event (GstBaseSink * sink, GstEvent * event)
{
  GstEventType type;
  PostcohFilesink *filesink;

  filesink = POSTCOH_FILESINK (sink);
  xmlTextWriterPtr writer = filesink->writer;

  type = GST_EVENT_TYPE (event);

  switch (type) {
    case GST_EVENT_EOS:
//      if (fflush (filesink->file))
//        goto flush_failed;

    /* for stream */
    xmlTextWriterEndElement(writer);

    /* for table */
    xmlTextWriterEndElement(writer);

    /* for the whole document */
    xmlTextWriterEndDocument(writer);

      break;
    default:
      break;
  }

  return GST_BASE_SINK_CLASS (parent_class)->event (sink, event);
#if 0
flush_failed:
  {
    GST_ELEMENT_ERROR (filesink, RESOURCE, WRITE,
        (_("Error while writing to file \"%s\"."), filesink->filename),
        GST_ERROR_SYSTEM);
    gst_event_unref (event);
    return FALSE;
  }
#endif
}



static GstFlowReturn
postcoh_filesink_render (GstBaseSink * sink, GstBuffer * buffer)
{
  PostcohFilesink *filesink = POSTCOH_FILESINK(sink);
  PostcohTable *table = (PostcohTable *) GST_BUFFER_DATA(buffer);
  PostcohTable *table_end = (PostcohTable *) (GST_BUFFER_DATA(buffer) + GST_BUFFER_SIZE(buffer));

  XmlTable *xtable = filesink->xtable;
  int rc;

  for(; table<table_end; table++) {
        GString *line = g_string_new("\t\t\t\t");
	g_string_append_printf(line, "%d%s", table->end_time.gpsSeconds, xtable->delimiter->str);
	g_string_append_printf(line, "%d%s", table->end_time.gpsNanoSeconds, xtable->delimiter->str);
	g_string_append_printf(line, "%d%s", table->is_background, xtable->delimiter->str);
	g_string_append_printf(line, "%s%s", table->ifos, xtable->delimiter->str);
	g_string_append_printf(line, "%s%s", table->pivotal_ifo, xtable->delimiter->str);
	g_string_append_printf(line, "%d%s", table->tmplt_idx, xtable->delimiter->str);
	g_string_append_printf(line, "%d%s", table->pix_idx, xtable->delimiter->str);
	g_string_append_printf(line, "%f%s", table->maxsnglsnr, xtable->delimiter->str);
	g_string_append_printf(line, "%f%s", table->cohsnr, xtable->delimiter->str);
	g_string_append_printf(line, "%f%s", table->nullsnr, xtable->delimiter->str);
	g_string_append_printf(line, "%f%s", table->chisq, xtable->delimiter->str);
	g_string_append_printf(line, "%s%s", table->skymap_fname, xtable->delimiter->str);
	
	g_string_append(line, "\n");
        rc = xmlTextWriterWriteFormatRaw(filesink->writer, line->str);
	if (rc < 0)
		return GST_FLOW_ERROR;
        g_string_free(line, TRUE);
  }

  return GST_FLOW_OK;
}

static gboolean
postcoh_filesink_start (GstBaseSink * basesink)
{
  PostcohFilesink *sink = POSTCOH_FILESINK (basesink);
  g_assert(sink->uri);

    sink->writer = xmlNewTextWriterFilename(sink->uri, sink->compress);
    xmlTextWriterPtr writer = sink->writer;
    if (writer == NULL) {
        printf("testXmlwriterFilename: Error creating the xml writer\n");
        return FALSE;
    }

    int rc;
    rc = xmlTextWriterSetIndent(writer, 1);
    rc = xmlTextWriterSetIndentString(writer, BAD_CAST "\t");

    /* Start the document with the xml default for the version,
     * encoding utf-8 and the default for the standalone
     * declaration. */
    rc = xmlTextWriterStartDocument(writer, NULL, MY_ENCODING, NULL);
    if (rc < 0) {
        printf
            ("testXmlwriterFilename: Error at xmlTextWriterStartDocument\n");
        return FALSE;
    }

    rc = xmlTextWriterWriteDTD(writer, BAD_CAST "LIGO_LW", NULL, BAD_CAST "http://ldas-sw.ligo.caltech.edu/doc/ligolwAPI/html/ligolw_dtd.txt", NULL);
    if (rc < 0) {
        printf
            ("testXmlwriterFilename: Error at xmlTextWriterWriteDTD\n");
        return FALSE;
    }

    /* Start an element named "LIGO_LW". Since thist is the first
     * element, this will be the root element of the document. */
    rc = xmlTextWriterStartElement(writer, BAD_CAST "LIGO_LW");
    if (rc < 0) {
        printf
            ("testXmlwriterFilename: Error at xmlTextWriterStartElement\n");
        return FALSE;
    }

    /* Start an element named "LIGO_LW" as child of EXAMPLE. */
    rc = xmlTextWriterStartElement(writer, BAD_CAST "LIGO_LW");
    if (rc < 0) {
        printf
            ("testXmlwriterFilename: Error at xmlTextWriterStartElement\n");
        return FALSE;
    }

  sink->xtable = (XmlTable *)malloc(sizeof(XmlTable));
  XmlTable * xtable = sink->xtable;
  postcoh_table_init(xtable);
    // Add the Table Node
    rc = xmlTextWriterStartElement(writer, BAD_CAST "Table");

    // Add Attribute "Name"
    rc = xmlTextWriterWriteAttribute(writer, BAD_CAST "Name", BAD_CAST xtable->tableName->str);

    // Write Column Nodes
    int i;
    for (i = 0; i < (int)xtable->names->len; ++i)
    {
        rc = xmlTextWriterStartElement(writer, BAD_CAST "Column");

        GString *colName = &g_array_index(xtable->names, GString, i);
        GString *type = &g_array_index(xtable->type_names, GString, i);


        rc = xmlTextWriterWriteAttribute(writer, BAD_CAST "Type", BAD_CAST type->str);
        rc = xmlTextWriterWriteAttribute(writer, BAD_CAST "Name", BAD_CAST colName->str);
        
        rc = xmlTextWriterEndElement(writer);
    }

    // Write Stream Nodes
    rc = xmlTextWriterStartElement(writer, BAD_CAST "Stream"); 
    rc = xmlTextWriterWriteAttribute(writer, BAD_CAST "Delimiter", BAD_CAST xtable->delimiter->str);
    rc = xmlTextWriterWriteAttribute(writer, BAD_CAST "Type", BAD_CAST "Local");
    rc = xmlTextWriterWriteAttribute(writer, BAD_CAST "Name", BAD_CAST xtable->tableName->str);
    
    rc = xmlTextWriterWriteString(writer, BAD_CAST "\n");
    if (rc < 0)
	    return FALSE;
    else
	    return TRUE;
}

static gboolean
postcoh_filesink_stop (GstBaseSink * basesink)
{

  PostcohFilesink *sink = POSTCOH_FILESINK (basesink);
  xmlFreeTextWriter(sink->writer);
//  postcoh_filesink_close_file (POSTCOH_FILESINK (basesink));
  if (sink->xtable)
	  free(sink->xtable);
  return TRUE;
}
/*** GSTURIHANDLER INTERFACE *************************************************/

static GstURIType
postcoh_filesink_uri_get_type (void)
{
  return GST_URI_SINK;
}

static gchar **
postcoh_filesink_uri_get_protocols (void)
{
  static gchar *protocols[] = { (char *) "file", NULL };

  return protocols;
}

static const gchar *
postcoh_filesink_uri_get_uri (GstURIHandler * handler)
{
  PostcohFilesink *sink = POSTCOH_FILESINK (handler);

  return sink->uri;
}

static gboolean
postcoh_filesink_uri_set_uri (GstURIHandler * handler, const gchar * uri)
{
  gchar *protocol, *location;
  gboolean ret;
  PostcohFilesink *sink = POSTCOH_FILESINK (handler);

  protocol = gst_uri_get_protocol (uri);
  if (strcmp (protocol, "file") != 0) {
    g_free (protocol);
    return FALSE;
  }
  g_free (protocol);

  /* allow file://localhost/foo/bar by stripping localhost but fail
   * for every other hostname */
  if (g_str_has_prefix (uri, "file://localhost/")) {
    char *tmp;

    /* 16 == strlen ("file://localhost") */
    tmp = g_strconcat ("file://", uri + 16, NULL);
    /* we use gst_uri_get_location() although we already have the
     * "location" with uri + 16 because it provides unescaping */
    location = gst_uri_get_location (tmp);
    g_free (tmp);
  } else if (strcmp (uri, "file://") == 0) {
    /* Special case for "file://" as this is used by some applications
     *  to test with gst_element_make_from_uri if there's an element
     *  that supports the URI protocol. */
    postcoh_filesink_set_location (sink, NULL);
    return TRUE;
  } else {
    location = gst_uri_get_location (uri);
  }

  if (!location)
    return FALSE;
  if (!g_path_is_absolute (location)) {
    g_free (location);
    return FALSE;
  }

  ret = postcoh_filesink_set_location (sink, location);
  g_free (location);

  return ret;
}

static void
postcoh_filesink_uri_handler_init (gpointer g_iface, gpointer iface_data)
{
  GstURIHandlerInterface *iface = (GstURIHandlerInterface *) g_iface;

  iface->get_type = postcoh_filesink_uri_get_type;
  iface->get_protocols = postcoh_filesink_uri_get_protocols;
  iface->get_uri = postcoh_filesink_uri_get_uri;
  iface->set_uri = postcoh_filesink_uri_set_uri;
}


import gettext
from typing import cast
from gi.repository import Gtk, Adw

_ = gettext.gettext

SHORTCUTS_XML = """
<interface domain="whisp">
  <object class="AdwShortcutsDialog" id="shortcuts_dialog">
    <child>
      <object class="AdwShortcutsSection">
        <property name="title">Application</property>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Preferences</property>
            <property name="action-name">win.preferences</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Show Shortcuts</property>
            <property name="action-name">win.show-shortcuts</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Toggle Slate Mode</property>
            <property name="action-name">win.slate-mode</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Quit Application</property>
            <property name="action-name">win.quit</property>
          </object>
        </child>
      </object>
    </child>

    <child>
      <object class="AdwShortcutsSection">
        <property name="title">Note Management</property>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Create New Note</property>
            <property name="action-name">win.new-note</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Search Notes</property>
            <property name="action-name">win.search</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Delete Note</property>
            <property name="action-name">win.delete-note</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Undo Delete</property>
            <property name="action-name">win.undo-delete</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Pin Note</property>
            <property name="action-name">win.pin-note</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Move Note to Front</property>
            <property name="action-name">win.bump-note</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Copy Entire Note</property>
            <property name="action-name">win.copy-note</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Export Note</property>
            <property name="action-name">win.export-note</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Toggle WYSIWYG</property>
            <property name="action-name">win.toggle-wysiwyg</property>
          </object>
        </child>
      </object>
    </child>

    <child>
      <object class="AdwShortcutsSection">
        <property name="title">Navigation</property>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Previous Note</property>
            <property name="action-name">win.nav-prev</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Next Note</property>
            <property name="action-name">win.nav-next</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">First Note</property>
            <property name="action-name">win.nav-first</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Last Note</property>
            <property name="action-name">win.nav-last</property>
          </object>
        </child>
      </object>
    </child>
    
    <child>
      <object class="AdwShortcutsSection">
        <property name="title">Editor Actions</property>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Toggle Checkbox</property>
            <property name="accelerator">&lt;Primary&gt;s</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Paste Plain Text</property>
            <property name="accelerator">&lt;Primary&gt;&lt;Shift&gt;v</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Shorten Selected URL</property>
            <property name="accelerator">&lt;Primary&gt;&lt;Shift&gt;l</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Bold Text</property>
            <property name="accelerator">&lt;Primary&gt;b</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Italic Text</property>
            <property name="accelerator">&lt;Primary&gt;i</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Underline Text</property>
            <property name="accelerator">&lt;Primary&gt;u</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Strikethrough Text</property>
            <property name="accelerator">&lt;Primary&gt;&lt;Shift&gt;s</property>
          </object>
        </child>
        <child>
          <object class="AdwShortcutsItem">
            <property name="title">Insert Emoji</property>
            <property name="accelerator">&lt;Primary&gt;period</property>
          </object>
        </child>
      </object>
    </child>
  </object>
</interface>
"""

def show_shortcuts_dialog(parent_win: Gtk.Window | None) -> Adw.ShortcutsDialog | Gtk.Window | None:
    builder = Gtk.Builder.new_from_string(SHORTCUTS_XML, -1)
    dialog = builder.get_object("shortcuts_dialog")
    if dialog:
        target_parent = parent_win if isinstance(parent_win, Gtk.Window) else None
        if isinstance(dialog, Adw.Dialog):
            dialog.present(target_parent)
        else:
            if hasattr(dialog, "set_transient_for") and target_parent:
                dialog.set_transient_for(target_parent)
            dialog.present()
    return dialog

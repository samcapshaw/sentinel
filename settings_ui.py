"""Theme-aware location and source controls, separate from the idle screensaver."""
import copy
import threading
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,Gdk,GLib
from sentinel import palette
from preferences import load_settings,save_settings,geocode,detect_location

class SettingsWindow(Gtk.Window):
    def __init__(self):
        GLib.set_prgname('org.omarchy.sentinel.settings')
        super().__init__(title='Sentinel · Observation Settings')
        self.set_default_size(650,780);self.set_border_width(28)
        self.settings=load_settings();self.locations=[];self.busy=False
        colors=palette();css=Gtk.CssProvider();css.load_from_data(('''window { background: BG; color: FG; } * { font-family: "JetBrains Mono"; font-size: 12px; } label.title { font-size: 23px; color: ACC; } label.muted { color: DIM; font-size: 11px; } entry, combobox button, spinbutton, button { background: BG; color: FG; border: 1px solid DIM; border-radius: 4px; padding: 8px; } button.suggested-action { color: ACC; border-color: ACC; } checkbutton { padding: 5px; }'''.replace('BG',colors['background']).replace('FG',colors['foreground']).replace('ACC',colors['accent']).replace('DIM',colors['dark_foreground'])).encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(),css,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14);self.add(box)
        title=Gtk.Label(label='SENTINEL / LOCATION',xalign=0);title.get_style_context().add_class('title');box.pack_start(title,False,False,0)
        self.label(box,'Choose the area your observation screens follow.',muted=True)
        self.mode=Gtk.ComboBoxText()
        for key,label in [('world','World — rotate through global observations'),('local','My location — enter a city or US ZIP code'),('auto','Automatic — approximate public-IP location')]:self.mode.append(key,label)
        self.mode.set_active_id(self.settings['mode']);box.pack_start(self.mode,False,False,0)
        row=Gtk.Box(spacing=8);self.query=Gtk.Entry();self.query.set_placeholder_text('City, country or US ZIP code')
        loc=self.settings.get('location');self.query.set_text(loc.get('query') or loc['name'] if loc else '')
        row.pack_start(self.query,True,True,0);search=Gtk.Button(label='Find');search.connect('clicked',self.search);row.pack_start(search,False,False,0);box.pack_start(row,False,False,0)
        self.results=Gtk.ComboBoxText();box.pack_start(self.results,False,False,0)
        if loc:self.set_results([loc])
        detect=Gtk.Button(label='Detect approximate location now');detect.connect('clicked',self.detect);box.pack_start(detect,False,False,0)
        self.label(box,'Auto sends your public IP to ipwho.is when selected and at launch.\nCity/ZIP search uses Zippopotam.us or Open-Meteo.\nYour saved location stays in your local settings file.',muted=True)
        grid=Gtk.Grid(column_spacing=18,row_spacing=8);box.pack_start(grid,False,False,0)
        self.radius=Gtk.SpinButton.new_with_range(25,500,25);self.radius.set_value(self.settings['radius_km'])
        self.quakes=Gtk.SpinButton.new_with_range(50,3000,50);self.quakes.set_value(self.settings['quake_radius_km'])
        self.units=Gtk.ComboBoxText();self.units.append('metric','°C / km');self.units.append('imperial','°F / miles');self.units.set_active_id(self.settings['units'])
        for y,(name,widget) in enumerate([('Local focus radius (km)',self.radius),('Earthquake radius (km)',self.quakes),('Weather units',self.units)]):grid.attach(Gtk.Label(label=name,xalign=0),0,y,1,1);grid.attach(widget,1,y,1,1)
        self.news=Gtk.Entry();self.news.set_placeholder_text('Optional news focus: e.g. Orange County California');self.news.set_text(self.settings['news_query']);box.pack_start(self.news,False,False,0)
        self.checks={};toggles=Gtk.FlowBox();toggles.set_selection_mode(Gtk.SelectionMode.NONE);toggles.set_max_children_per_line(3);toggles.set_min_children_per_line(3)
        for key,label in [('cameras','Cameras'),('flights','Flights'),('roads','Roads'),('earthquakes','Earthquakes'),('weather','Weather / storms'),('news','News events'),('satellites','Satellites')]:
            check=Gtk.CheckButton(label=label);check.set_active(self.settings['sections'][key]);self.checks[key]=check;toggles.add(check)
        box.pack_start(toggles,False,False,0)
        self.status=Gtk.Label(label='Save applies on the next screensaver launch.',xalign=0);self.status.set_line_wrap(True);box.pack_start(self.status,False,False,0)
        save=Gtk.Button(label='Save settings');save.get_style_context().add_class('suggested-action');save.connect('clicked',self.save);box.pack_end(save,False,False,0)
        self.connect('delete-event',lambda *args:Gtk.main_quit());self.show_all()
    def label(self,box,text,muted=False):
        label=Gtk.Label(label=text,xalign=0);label.set_line_wrap(True)
        if muted:label.get_style_context().add_class('muted')
        box.pack_start(label,False,False,0)
    def set_results(self,locations):
        self.locations=locations;self.results.remove_all()
        for loc in locations:self.results.append_text(f"{loc['name']}, {loc['region']} / {loc['country']}  ({loc['lat']:.3f}, {loc['lon']:.3f})")
        self.results.set_active(0)
    def task(self,fn,mode):
        if self.busy:return
        self.busy=True;self.status.set_text('Looking up location…')
        def worker():
            try:results=fn();GLib.idle_add(self.completed,results,mode,None)
            except Exception as exc:GLib.idle_add(self.completed,None,mode,str(exc))
        threading.Thread(target=worker,daemon=True).start()
    def completed(self,results,mode,error):
        self.busy=False
        if error:self.status.set_text('Location lookup failed: '+error[:150]);return False
        self.set_results(results);self.mode.set_active_id(mode);self.status.set_text('Location resolved. Review the result, then save.');return False
    def search(self,*args):
        query=self.query.get_text();self.task(lambda:geocode(query),'local')
    def detect(self,*args):self.task(lambda:[detect_location()],'auto')
    def save(self,*args):
        if self.busy:return
        mode=self.mode.get_active_id();index=self.results.get_active()
        if mode!='world' and (index<0 or index>=len(self.locations)):
            self.status.set_text('Find or detect your location first.');return
        settings=copy.deepcopy(self.settings);settings.update(mode=mode,location=self.locations[index] if index>=0 else None,radius_km=self.radius.get_value_as_int(),quake_radius_km=self.quakes.get_value_as_int(),news_query=self.news.get_text(),units=self.units.get_active_id(),sections={key:widget.get_active() for key,widget in self.checks.items()})
        try:save_settings(settings)
        except OSError as exc:self.status.set_text('Could not save: '+str(exc));return
        self.settings=settings;self.status.set_text('Saved. Your next screensaver launch will use these settings.')

def main():SettingsWindow();Gtk.main()

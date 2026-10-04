import sys, types, threading
from pathlib import Path

# ---- minimal pwnagotchi runtime stubs ----
# Marked __TWEAKVIEW_STUB__ so the real-framework harness in tests/integration/
# can recognize and evict these if both suites share one interpreter.
pwn = types.ModuleType('pwnagotchi'); pwn.__version__='2.9.5.8'; pwn.config={}; pwn.__TWEAKVIEW_STUB__=True
plugins = types.ModuleType('pwnagotchi.plugins'); plugins.__TWEAKVIEW_STUB__=True
class Plugin: pass
plugins.Plugin=Plugin
fonts = types.ModuleType('pwnagotchi.ui.fonts')
class Font:
    def __init__(self,name,size=10): self.name=name; self.size=size; self.path=name
    def __eq__(self,o): return self is o
for n,s in [('Small',8),('BoldSmall',8),('Medium',10),('Bold',10),('BoldBig',12),('Huge',18)]: setattr(fonts,n,Font(n,s))
components = types.ModuleType('pwnagotchi.ui.components')
class Widget:
    def __init__(self,xy,color=0): self.xy=xy; self.color=color
    def draw(self,*a): pass
class Line(Widget):
    def __init__(self,xy,color=0,width=1): super().__init__(xy,color); self.width=width
class Rect(Widget): pass
class FilledRect(Widget): pass
class Text(Widget):
    def __init__(self,value='',position=(0,0),font=None,color=0,wrap=False,max_length=0,png=False):
        super().__init__(position,color); self.value=value; self.font=font; self.wrap=wrap; self.max_length=max_length; self.wrapper=None; self.png=png
class LabeledValue(Widget):
    def __init__(self,label,value='',position=(0,0),label_font=None,text_font=None,color=0,label_spacing=5):
        super().__init__(position,color); self.label=label; self.value=value; self.label_font=label_font; self.text_font=text_font; self.label_spacing=label_spacing
for c in [Widget,Line,Rect,FilledRect,Text,LabeledValue]: setattr(components,c.__name__,c)
ui=types.ModuleType('pwnagotchi.ui')
for _m in (fonts,components,ui): _m.__TWEAKVIEW_STUB__=True
sys.modules.update({'pwnagotchi':pwn,'pwnagotchi.plugins':plugins,'pwnagotchi.ui':ui,'pwnagotchi.ui.fonts':fonts,'pwnagotchi.ui.components':components})
pwn.plugins=plugins

# flask stubs: sufficient for import and direct lifecycle/unit tests
flask=types.ModuleType('flask'); flask.__TWEAKVIEW_STUB__=True
def abort(code): raise RuntimeError(f'abort:{code}')
def jsonify(obj): return obj
def render_template_string(s,**kw): return s
flask.abort=abort; flask.jsonify=jsonify; flask.render_template_string=render_template_string
sys.modules['flask']=flask

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

class FakeState:
    def __init__(self,state=None): self._state=dict(state or {}); self._lock=threading.Lock(); self._changes={}
    def add_element(self,k,v): self._state[k]=v; self._changes[k]=True
    def remove_element(self,k): del self._state[k]; self._changes[k]=True

class FakeView:
    def __init__(self,w=480,h=320):
        self._w=w; self._h=h; self._lock=threading.Lock(); self._state=FakeState(); self.invert=0; self.updates=0
    def width(self): return self._w
    def height(self): return self._h
    def add_element(self,k,v): self._state.add_element(k,v)
    def remove_element(self,k): self._state.remove_element(k)
    def update(self,force=False): self.updates += 1

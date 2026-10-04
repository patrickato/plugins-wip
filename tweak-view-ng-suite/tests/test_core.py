import json, os, threading
import pytest
from conftest import FakeView
import tweak_view_ng as tv
from pwnagotchi.ui.components import Text, Line, LabeledValue
import pwnagotchi.ui.fonts as fonts


def registry(): return {'Small':fonts.Small,'Bold':fonts.Bold,'Huge':fonts.Huge}

def test_adapter_dimensions_and_snapshot():
    v=FakeView(480,320); v.add_element('face',Text('x',(10,20),font=fonts.Huge,color=255)); a=tv.JayUIAdapter(v,registry())
    s=a.snapshot(); assert s['screen']=={'width':480,'height':320}; assert s['elements']['face']['properties']['xy']==[10,20]; assert s['elements']['face']['properties']['font']=='Huge'

def test_apply_xy_clamps_to_screen():
    v=FakeView(100,50); v.add_element('face',Text('x',(10,10),font=fonts.Huge)); a=tv.JayUIAdapter(v,registry()); orig={}
    changed=a.apply_properties('face',{'xy':'999,-1'},orig); assert 'xy' in changed; assert tuple(v._state._state['face'].xy)==(99,49); assert orig['face']['xy']==(10,10)

def test_line_requires_four_coords_and_clamps():
    v=FakeView(100,50); v.add_element('line',Line((1,2,3,4),width=1)); a=tv.JayUIAdapter(v,registry())
    a.apply_properties('line',{'xy':'-1,-1,999,999'},{}); assert v._state._state['line'].xy==(99,49,99,49)
    with pytest.raises(ValueError): a.apply_properties('line',{'xy':'1,2'}, {})

def test_font_validation():
    v=FakeView(); v.add_element('t',Text('x',(0,0),font=fonts.Small)); a=tv.JayUIAdapter(v,registry())
    a.apply_properties('t',{'font':'Huge'},{}); assert v._state._state['t'].font is fonts.Huge
    with pytest.raises(ValueError): a.apply_properties('t',{'font':'MadeUp'}, {})

def test_unknown_property_is_ignored_by_apply():
    v=FakeView(); v.add_element('t',Text('x',(0,0),font=fonts.Small)); a=tv.JayUIAdapter(v,registry())
    assert a.apply_properties('t',{'value':'evil','made_up':3},{})==[]; assert v._state._state['t'].value=='x'

def test_restore_original():
    v=FakeView(); v.add_element('t',Text('x',(3,4),font=fonts.Small)); a=tv.JayUIAdapter(v,registry()); orig={}
    a.apply_properties('t',{'xy':[20,30],'font':'Huge'},orig); a.restore_properties('t',orig['t']); w=v._state._state['t']; assert tuple(w.xy)==(3,4); assert w.font is fonts.Small

def test_shapes_add_replace_remove():
    v=FakeView(80,60); a=tv.JayUIAdapter(v,registry())
    a.add_shape('r',{'type':'rect','properties':{'xy':[1,2,50,40],'color':255}}); assert 'r' in v._state._state
    a.add_shape('r',{'type':'rect','properties':{'xy':[2,3,30,20],'color':0}}); assert tuple(v._state._state['r'].xy)==(2,3,30,20)
    assert a.remove('r') is True; assert a.remove('r') is False

def test_legacy_import():
    old={'VSS.face.xy':'10,20','VSS.face.font':'Huge','VSS.status.max_length':'30','nonsense':1,'__custom_shapes__':{'box':{'type':'CustomRect','props':{'xy':'1,2,3,4','color':255}}}}
    p=tv.import_legacy(old); assert p['edits']['face']=={'xy':'10,20','font':'Huge'}; assert p['edits']['status']['max_length']=='30'; assert p['shapes']['box']['type']=='rect'

def test_store_roundtrip_and_backup(tmp_path):
    fn=tmp_path/'cfg.json'; st=tv.LayoutStore(str(fn),True); d=st.empty(); st.save(d); d['active_profile']='x'; d['profiles']['x']={'edits':{},'shapes':{}}; st.save(d)
    assert st.load()['active_profile']=='x'; assert (tmp_path/'cfg.json.bak').exists(); json.load(open(fn))

def test_store_rejects_wrong_schema(tmp_path):
    fn=tmp_path/'bad.json'; fn.write_text('{"schema":99}')
    with pytest.raises(ValueError): tv.LayoutStore(str(fn)).load()

def test_plugin_merges_defaults():
    p=tv.TweakViewNG(); p.options={'history_limit':5}; p.on_loaded(); assert p.options['filename'].endswith('tweak_view_ng.json'); assert p.options['history_limit']==5

def test_plugin_ui_setup_applies_saved_edit(tmp_path,monkeypatch):
    # Layout exists on disk before on_loaded() preloads it (the real lifecycle).
    st=tv.LayoutStore(str(tmp_path/'ng.json'))
    d=st.empty(); d['profiles']['default']['edits']={'face':{'xy':[100,120],'font':'Huge'}}; st.save(d)
    p=tv.TweakViewNG(); p.options={'filename':str(tmp_path/'ng.json'),'legacy_filename':str(tmp_path/'legacy.json')}
    monkeypatch.setattr(p,'_build_fonts',lambda: setattr(p,'_fonts',registry()))
    p.on_loaded()
    v=FakeView(480,320); v.add_element('face',Text('x',(1,2),font=fonts.Small))
    p.on_ui_setup(v); assert tuple(v._state._state['face'].xy)==(100,120); assert v._state._state['face'].font is fonts.Huge

def test_pending_element_applies_when_appears(tmp_path,monkeypatch):
    st=tv.LayoutStore(str(tmp_path/'ng.json')); d=st.empty(); d['profiles']['default']['edits']={'later':{'xy':[7,8]}}; st.save(d)
    p=tv.TweakViewNG(); p.options={'filename':str(tmp_path/'ng.json'),'legacy_filename':str(tmp_path/'none')}; monkeypatch.setattr(p,'_build_fonts',lambda: setattr(p,'_fonts',registry())); p.on_loaded()
    v=FakeView(); p.on_ui_setup(v); assert 'later' in p._pending_missing
    v.add_element('later',Text('x',(0,0),font=fonts.Small)); p.on_ui_update(v); assert tuple(v._state._state['later'].xy)==(7,8); assert 'later' not in p._pending_missing

def test_unload_restores_and_removes_shapes(tmp_path,monkeypatch):
    st=tv.LayoutStore(str(tmp_path/'ng.json')); d=st.empty(); d['profiles']['default']['edits']={'face':{'xy':[9,9]}}; d['profiles']['default']['shapes']={'r':{'type':'rect','properties':{'xy':[1,1,5,5]}}}; st.save(d)
    p=tv.TweakViewNG(); p.options={'filename':str(tmp_path/'ng.json'),'legacy_filename':str(tmp_path/'none')}; monkeypatch.setattr(p,'_build_fonts',lambda: setattr(p,'_fonts',registry())); p.on_loaded()
    v=FakeView(); v.add_element('face',Text('x',(2,3),font=fonts.Small)); p.on_ui_setup(v); assert 'r' in v._state._state; p.on_unload(v); assert tuple(v._state._state['face'].xy)==(2,3); assert 'r' not in v._state._state

def test_locking_survives_parallel_snapshot_and_edits():
    v=FakeView(); v.add_element('t',Text('x',(0,0),font=fonts.Small)); a=tv.JayUIAdapter(v,registry()); errs=[]
    def reader():
        try:
            for _ in range(300): a.snapshot()
        except Exception as e: errs.append(e)
    def writer():
        try:
            for i in range(300): a.apply_properties('t',{'xy':[i%480,i%320]}, {})
        except Exception as e: errs.append(e)
    ts=[threading.Thread(target=reader),threading.Thread(target=writer)]; [t.start() for t in ts]; [t.join() for t in ts]; assert not errs

class Req:
    def __init__(self, method='GET', data=None): self.method=method; self._data=data
    def get_json(self, silent=True): return self._data


def setup_plugin(tmp_path, monkeypatch):
    p=tv.TweakViewNG(); p.options={'filename':str(tmp_path/'ng.json'),'legacy_filename':str(tmp_path/'legacy.json'),'backup':True}; p.on_loaded()
    monkeypatch.setattr(p,'_build_fonts',lambda: setattr(p,'_fonts',registry()))
    v=FakeView(480,320); v.add_element('face',Text('x',(2,3),font=fonts.Small)); v.add_element('status',Text('hello',(10,20),font=fonts.Bold,wrap=True,max_length=20))
    p.on_ui_setup(v)
    return p,v


def test_api_update_persist_undo_redo(tmp_path, monkeypatch):
    p,v=setup_plugin(tmp_path,monkeypatch)
    r=p._route_api('api/update',Req('POST',{'element':'face','properties':{'xy':[100,101],'font':'Huge'}}))
    assert r['ok']; assert tuple(v._state._state['face'].xy)==(100,101); assert v._state._state['face'].font is fonts.Huge
    saved=json.load(open(tmp_path/'ng.json')); assert saved['profiles']['default']['edits']['face']['xy']==[100,101]
    r=p._route_api('api/undo',Req('POST',{})); assert r['ok']; assert tuple(v._state._state['face'].xy)==(2,3); assert v._state._state['face'].font is fonts.Small
    r=p._route_api('api/redo',Req('POST',{})); assert r['ok']; assert tuple(v._state._state['face'].xy)==(100,101); assert v._state._state['face'].font is fonts.Huge


def test_api_revert_element_restores_only_that_element(tmp_path, monkeypatch):
    p,v=setup_plugin(tmp_path,monkeypatch)
    p._route_api('api/update',Req('POST',{'element':'face','properties':{'xy':[40,41]}}))
    p._route_api('api/update',Req('POST',{'element':'status','properties':{'xy':[50,51]}}))
    p._route_api('api/revert',Req('POST',{'element':'face'}))
    assert tuple(v._state._state['face'].xy)==(2,3); assert tuple(v._state._state['status'].xy)==(50,51)
    assert 'face' not in p._profile()['edits']; assert 'status' in p._profile()['edits']


def test_api_shape_persists_updates_and_delete(tmp_path, monkeypatch):
    p,v=setup_plugin(tmp_path,monkeypatch)
    assert p._route_api('api/add_shape',Req('POST',{'name':'border','type':'rect','properties':{'xy':[1,2,100,90],'color':255}}))['ok']
    assert 'border' in v._state._state
    r=p._route_api('api/update',Req('POST',{'element':'border','properties':{'xy':[3,4,110,95],'color':0}})); assert r['ok']
    assert tuple(v._state._state['border'].xy)==(3,4,110,95)
    saved=json.load(open(tmp_path/'ng.json')); assert saved['profiles']['default']['shapes']['border']['properties']['xy']==[3,4,110,95]
    assert p._route_api('api/delete_shape',Req('POST',{'name':'border'}))['ok']; assert 'border' not in v._state._state


def test_api_reset_restores_all_and_clears_profile(tmp_path, monkeypatch):
    p,v=setup_plugin(tmp_path,monkeypatch)
    p._route_api('api/update',Req('POST',{'element':'face','properties':{'xy':[40,41]}}))
    p._route_api('api/add_shape',Req('POST',{'name':'linex','type':'line','properties':{'xy':[0,0,20,20]}}))
    p._route_api('api/reset',Req('POST',{}))
    assert tuple(v._state._state['face'].xy)==(2,3); assert 'linex' not in v._state._state; assert p._profile()=={'edits':{},'shapes':{}}


def test_auto_import_legacy_preserves_legacy_file(tmp_path, monkeypatch):
    legacy=tmp_path/'legacy.json'; legacy.write_text(json.dumps({'VSS.face.xy':'44,55'}))
    p=tv.TweakViewNG(); p.options={'filename':str(tmp_path/'ng.json'),'legacy_filename':str(legacy),'auto_import_legacy':True}; p.on_loaded(); monkeypatch.setattr(p,'_build_fonts',lambda: setattr(p,'_fonts',registry()))
    v=FakeView(); v.add_element('face',Text('x',(1,2),font=fonts.Small)); before=legacy.read_text(); p.on_ui_setup(v)
    assert tuple(v._state._state['face'].xy)==(44,55); assert legacy.read_text()==before; assert (tmp_path/'ng.json').exists()


def test_import_rejects_unknown_schema_without_changing_layout(tmp_path, monkeypatch):
    p,v=setup_plugin(tmp_path,monkeypatch); before=p._snapshot_config(); r=p._route_api('api/import',Req('POST',{'schema':999,'profiles':{}}))
    assert isinstance(r,tuple) and r[1]==400; assert p._layout==before


def test_profile_switch_restores_previous_and_applies_new(tmp_path, monkeypatch):
    p,v=setup_plugin(tmp_path,monkeypatch)
    p._route_api('api/update',Req('POST',{'element':'face','properties':{'xy':[20,21]}}))
    p._route_api('api/profile',Req('POST',{'name':'alt'})); assert tuple(v._state._state['face'].xy)==(2,3)
    p._route_api('api/update',Req('POST',{'element':'face','properties':{'xy':[70,71]}})); assert tuple(v._state._state['face'].xy)==(70,71)
    p._route_api('api/profile',Req('POST',{'name':'default'})); assert tuple(v._state._state['face'].xy)==(20,21)


def test_status_max_length_rebuilds_wrapper(tmp_path, monkeypatch):
    p,v=setup_plugin(tmp_path,monkeypatch)
    p._route_api('api/update',Req('POST',{'element':'status','properties':{'max_length':8}}))
    w=v._state._state['status']; assert w.max_length==8; assert w.wrapper is not None and w.wrapper.width==8


def test_web_ui_has_no_external_dependencies():
    lower=tv.WEB_UI.lower(); assert 'fonts.googleapis.com' not in lower; assert 'http://' not in lower; assert 'https://' not in lower

@pytest.mark.parametrize('size',[(250,122),(296,128),(400,300),(480,320),(800,480),(320,480),(480,800)])
def test_resolution_matrix(size):
    w,h=size; v=FakeView(w,h); v.add_element('t',Text('x',(0,0),font=fonts.Small)); a=tv.JayUIAdapter(v,registry())
    a.apply_properties('t',{'xy':[w+100,h+100]},{}); assert tuple(v._state._state['t'].xy)==(w-1,h-1); assert a.snapshot()['screen']=={'width':w,'height':h}


def test_thousand_edit_stress():
    v=FakeView(480,320); v.add_element('t',Text('x',(0,0),font=fonts.Small)); a=tv.JayUIAdapter(v,registry()); orig={}
    for i in range(1000): a.apply_properties('t',{'xy':[i%500,i%350]},orig)
    x,y=v._state._state['t'].xy; assert 0<=x<480 and 0<=y<320; assert orig['t']['xy']==(0,0)

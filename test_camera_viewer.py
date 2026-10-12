import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from camera_viewer import CameraViewer, camera_html, DWELL

class CameraTests(unittest.TestCase):
    def viewer(self):
        v=CameraViewer.__new__(CameraViewer)
        v.cameras=[{'video_id':'a'},{'video_id':'b'}]
        v.index=-1;v.cursor=-1;v.active=False;v.started=0;v.status='CONNECTING';v.failures={};v.played=False;v.last_error=''
        v.current=None;v.pending=None;v.generation=0;v.fixed=MagicMock();v.server=SimpleNamespace(server_port=1234)
        v.canvas=MagicMock();v.canvas.get_allocated_width.return_value=1600;v.canvas.get_allocated_height.return_value=1000
        v.slots=[{'web':MagicMock(),'index':-1,'generation':-1,'started':0,'playing_at':None} for _ in range(2)]
        return v

    def playing(self,v,number):
        slot=v.slots[number];result=MagicMock()
        result.get_js_value.return_value.to_string.return_value=json.dumps({'video':v.cameras[slot['index']]['video_id'],'generation':slot['generation'],'state':'playing'})
        v.message(None,result,number)

    @patch('camera_viewer.time.monotonic')
    def test_startup_is_hidden_until_video_has_settled(self,clock):
        clock.return_value=100;v=self.viewer();v.update(True)
        web=v.slots[0]['web'];web.set_opacity.assert_called_with(0)
        self.playing(v,0)
        clock.return_value=102;v.update(True);self.assertIsNone(v.current)
        clock.return_value=104;v.update(True)
        self.assertEqual(v.current,0);web.set_opacity.assert_called_with(1);self.assertTrue(v.played)

    @patch('camera_viewer.time.monotonic')
    def test_next_preloads_without_replacing_current_until_dwell(self,clock):
        clock.return_value=100;v=self.viewer();v.update(True);self.playing(v,0)
        clock.return_value=104;v.update(True)
        clock.return_value=104+DWELL-9;v.update(True);self.assertEqual(v.pending,1)
        self.playing(v,1);v.slots[1]['web'].set_opacity.assert_called_with(0)
        clock.return_value=104+DWELL-1;v.update(True);self.assertEqual(v.current,0)
        clock.return_value=104+DWELL;v.update(True);self.assertEqual(v.current,1)
        v.slots[1]['web'].set_opacity.assert_called_with(1)
        v.slots[0]['web'].load_uri.assert_called_with('about:blank')

    @patch('camera_viewer.time.monotonic')
    def test_failure_keeps_current_and_backs_off_failed_camera(self,clock):
        clock.return_value=100;v=self.viewer();v.update(True);self.playing(v,0)
        clock.return_value=104;v.update(True)
        clock.return_value=140;v.update(True);v.failed(1,'error 150')
        self.assertEqual(v.current,0);self.assertGreater(v.failures[1],clock.return_value)
        v.slots[1]['web'].set_opacity.assert_called_with(0)
        v.update(False);self.assertFalse(v.active);self.assertIsNone(v.current)

    def test_html_mutes_before_playing_and_reports_generation(self):
        html=camera_html('abc123',7)
        self.assertIn('e.target.mute();e.target.playVideo()',html)
        self.assertIn('generation:7',html)
        self.assertIn('origin:location.origin',html)

    def test_direct_hls_uses_bundled_player_and_images_refresh(self):
        html=camera_html({'media_url':'https://example.org/live.m3u8','kind':'hls'},9)
        self.assertIn('src="/hls.js"',html)
        self.assertIn('hls.loadSource(',html)
        image=camera_html({'media_url':'https://example.org/camera.jpg','kind':'image'},10)
        self.assertIn('setInterval(refresh,15000)',image)
        self.assertIn("incoming.onload",image)

    @patch('camera_viewer.time.monotonic')
    def test_stale_player_message_cannot_reveal_new_source(self,clock):
        clock.return_value=100;v=self.viewer();v.update(True)
        result=MagicMock();result.get_js_value.return_value.to_string.return_value=json.dumps({'video':'a','generation':-1,'state':'playing'})
        v.message(None,result,0)
        clock.return_value=110;v.update(True)
        self.assertIsNone(v.current)
        v.slots[0]['web'].set_opacity.assert_called_with(0)

    @patch('camera_viewer.time.monotonic')
    def test_hls_timeout_falls_back_to_snapshot_after_six_seconds(self,clock):
        v=self.viewer();v.cameras[0]={'kind':'hls','media_url':'https://example.org/live.m3u8','fallback_url':'https://example.org/frame.jpg'}
        clock.return_value=100;v.update(True)
        clock.return_value=107;v.update(True)
        self.assertEqual(v.cameras[0]['kind'],'image');self.assertIsNone(v.pending)
        v.update(True);self.assertEqual(v.slots[v.pending]['index'],0)

    @patch('camera_viewer.time.monotonic')
    def test_stalled_current_video_is_not_left_visible_indefinitely(self,clock):
        clock.return_value=100;v=self.viewer();v.update(True);self.playing(v,0)
        clock.return_value=104;v.update(True);v.slots[0]['buffering_at']=105
        clock.return_value=111;v.update(True)
        self.assertIsNone(v.current);self.assertGreater(v.failures[0],111)

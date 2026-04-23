# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####


# Fix Blender 5.1 compatibility
# - Fix selected_sequences error
# - Fix sequences_all error
# - Add UI panel with threshold slider



bl_info = {
    "name": "Detect Shots and Split Strips",
    "author": "Tintwotin, Brandon Castellano(PySceneDetect-module)",
    "version": (1, 3),
    "blender": (4, 5, 0),
    "location": "Sequencer > Sidebar > Detect Shots",
    "description": "Detect shots in active strip and split all selected strips accordingly.",
    "warning": "",
    "doc_url": "",
    "category": "Sequencer",
}

import bpy, subprocess, os, sys
from bpy.types import Operator, Panel
from bpy.props import (
    IntProperty,
    BoolProperty,
    EnumProperty,
    StringProperty,
    FloatProperty,
)

def find_scenes(video_path, threshold, start, end):
    pybin = sys.executable
    try:
        subprocess.call([pybin, "-m", "ensurepip"])
    except ImportError:
        pass
    try:
        from scenedetect import open_video
        from scenedetect import SceneManager
        from scenedetect.detectors import ContentDetector
    except ImportError:
        subprocess.check_call([pybin, "-m", "pip", "install", "scenedetect[opencv]"])
        from scenedetect import open_video
        from scenedetect import SceneManager
        from scenedetect.detectors import ContentDetector
        
    render = bpy.context.scene.render
    fps = round((render.fps / render.fps_base), 3)
    video = open_video(video_path, framerate=fps)
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=threshold))
    video.seek((start/fps))
    scene_manager.detect_scenes(video, end_time=(end/fps))

    return scene_manager.get_scene_list()

# ------------------------------
# 终极兼容：获取选中序列 (Blender 4.5 & 5.1)
# ------------------------------
def get_selected_sequences(context):
    seq_ed = context.scene.sequence_editor
    if not seq_ed:
        return []
    return [s for s in seq_ed.strips if s.select]

class SEQUENCER_OT_split_selected(bpy.types.Operator):
    """Split Unlocked Un/Seleted Strips Soft"""

    bl_idname = "sequencer.split_selected"
    bl_label = "Split Selected"
    bl_options = {"REGISTER", "UNDO"}

    type: EnumProperty(
        name="Type",
        description="Split Type",
        items=(
            ("SOFT", "Soft", "Split Soft"),
            ("HARD", "Hard", "Split Hard"),
        ),
    )

    @classmethod
    def poll(cls, context):
        return (context.area and context.area.type == 'SEQUENCE_EDITOR' and
                context.scene.sequence_editor is not None)

    def execute(self, context):
        selection = get_selected_sequences(context)
        sequences = context.scene.sequence_editor.strips
        cf = context.scene.frame_current
        at_cursor = []
        cut_selected = False

        for s in sequences:
            if s.frame_final_start <= cf and s.frame_final_end > cf:
                if not s.lock:
                    at_cursor.append(s)
                    if s.select:
                        cut_selected = True
                        
        for s in at_cursor:
            if cut_selected and s.select:
                bpy.ops.sequencer.select_all(action="DESELECT")
                s.select = True
                bpy.ops.sequencer.split(
                    frame=cf,
                    type=self.type,
                    side="RIGHT"
                )
                
                current_selected = get_selected_sequences(context)
                for strip in current_selected:
                    if strip not in selection:
                        selection.append(strip)
                        
                bpy.ops.sequencer.select_all(action="DESELECT")
                for strip in selection:
                    strip.select = True
                    
        return {"FINISHED"}


class SEQUENCER_OT_detect_shots(Operator):
    """Detect shots in active strip and split all selected strips accordingly"""

    bl_idname = "sequencer.detect_shots"
    bl_label = "Detect Shots & Split Strips"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        seq_ed = context.scene.sequence_editor
        return seq_ed and seq_ed.active_strip and seq_ed.active_strip.type == "MOVIE"

    def execute(self, context):
        scene = context.scene
        cf = context.scene.frame_current
        active = context.scene.sequence_editor.active_strip
        
        path = os.path.realpath(bpy.path.abspath(active.filepath))        
        start_time = active.frame_offset_start
        end_time = active.frame_duration - active.frame_offset_end
        threshold = scene.shot_threshold
        
        scenes = find_scenes(path, threshold, start_time, end_time)
        
        for scene_data in scenes:
            frame = int(scene_data[1].get_frames() + active.frame_start)
            context.scene.frame_current = frame
            bpy.ops.sequencer.split_selected(type='SOFT')

        context.scene.frame_current = cf
        self.report({'INFO'}, "Shot detection completed!")
        return {'FINISHED'}


# ====================== 面板 UI ======================
class SEQUENCER_PT_detect_shots_panel(Panel):
    bl_label = "Detect Shots"
    bl_idname = "SEQUENCER_PT_detect_shots_panel"
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Detect Shots"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "shot_threshold", text="Threshold")
        layout.operator("sequencer.detect_shots", icon="SCENE")


# ====================== 注册 ======================
classes = (
    SEQUENCER_OT_detect_shots,
    SEQUENCER_OT_split_selected,
    SEQUENCER_PT_detect_shots_panel,
)

def register():
    bpy.types.Scene.shot_threshold = FloatProperty(
        name="Threshold",
        description="Lower = more cuts",
        default=32.0,
        min=1.0,
        max=100.0,
    )
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    del bpy.types.Scene.shot_threshold
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()

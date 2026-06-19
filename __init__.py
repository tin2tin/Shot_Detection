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

bl_info = {
    "name": "Detect Shots and Split Strips",
    "author": "Tintwotin, Brandon Castellano(PySceneDetect-module)",
    "version": (1, 5),
    "blender": (5, 2, 0),
    "location": "Sequencer > Strip Menu, Context Menu or Sidebar",
    "description": "Detect shots asynchronously in all selected movie strips and split accordingly.",
    "warning": "",
    "doc_url": "",
    "category": "Sequencer",
}

import bpy, subprocess, os, sys, threading
from bpy.types import Operator, Panel
from bpy.props import (
    IntProperty,
    BoolProperty,
    EnumProperty,
    StringProperty,
    FloatProperty,
)

# Global tracker for background processes
_queue_state = None


def get_strip_prop(strip, prop_name):
    """
    Robust property getter supporting both pre-Blender 5.1 and Blender 5.1+ property renames.
    """
    mapping = {
        "frame_offset_start": "left_handle_offset",
        "frame_offset_end": "right_handle_offset",
        "frame_duration": "content_duration",
        "frame_start": "content_start",
        "frame_final_start": "left_handle",
        "frame_final_end": "right_handle",
        "frame_final_duration": "duration",
    }
    new_prop = mapping.get(prop_name, prop_name)
    if hasattr(strip, new_prop):
        return getattr(strip, new_prop)
    elif hasattr(strip, prop_name):
        return getattr(strip, prop_name)
    return 0


def get_vse_context_override():
    """
    Locates the active Video Sequence Editor screen elements to construct a valid 
    context override for background timer execution.
    """
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type == 'SEQUENCE_EDITOR':
                region = next((r for r in area.regions if r.type == 'WINDOW'), area.regions[-1])
                return {
                    "window": window,
                    "screen": screen,
                    "area": area,
                    "region": region,
                }
    return None


def force_vse_redraw():
    """
    Refreshes any open Video Sequence Editor interfaces.
    """
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'SEQUENCE_EDITOR':
                area.tag_redraw()


def check_dependencies():
    """
    Ensures scenedetect package dependencies are available inside Python.
    """
    pybin = sys.executable
    try:
        subprocess.call([pybin, "-m", "ensurepip"])
    except ImportError:
        pass
    try:
        import scenedetect
    except ImportError:
        subprocess.check_call([pybin, "-m", "pip", "install", "scenedetect[opencv]"])


def bg_worker(job, fps):
    """
    Executes scene detection for a specific job in a background thread.
    Do not call Blender API (bpy) methods inside this function.
    """
    try:
        from scenedetect import open_video, FrameTimecode
        from scenedetect import SceneManager
        from scenedetect.detectors import ContentDetector
        
        video = open_video(job["path"], framerate=fps)
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=job["threshold"]))
        
        # Verify and clamp frame values inside the actual video file limits
        total_frames = video.duration.get_frames()
        start_frame = max(0, min(int(job["start_time"]), total_frames - 1))
        end_frame = max(start_frame + 1, min(int(job["end_time"]), total_frames))
        
        # Build precise FrameTimecode representations using custom FPS
        start_tc = FrameTimecode(timecode=start_frame, fps=fps)
        end_tc = FrameTimecode(timecode=end_frame, fps=fps)
        
        video.seek(start_tc)
        scene_manager.detect_scenes(video, end_time=end_tc)
        
        scenes = scene_manager.get_scene_list()
        
        # Pull basic integers out of scenedetect timecode structures
        job["result"] = [int(s_scene[1].get_frames()) for s_scene in scenes]
        job["status"] = "COMPLETED"
    except Exception as e:
        import traceback
        job["error"] = f"{e}\n{traceback.format_exc()}"
        job["status"] = "FAILED"


def prepare_selection_for_job(job, originally_selected):
    """
    Deselects all items, then selects only the target movie strip and any originally 
    selected sibling strips (like audio) that overlap with it on the timeline.
    """
    bpy.ops.sequencer.select_all(action='DESELECT')
    
    target_strip = bpy.context.sequencer_scene.sequence_editor.strips.get(job["strip_name"])
    if target_strip:
        target_strip.select = True
        
        t_start = get_strip_prop(target_strip, "frame_final_start")
        t_end = get_strip_prop(target_strip, "frame_final_end")
        
        for s in bpy.context.sequencer_scene.sequence_editor.strips_all:
            # If a strip is in the originally selected set, is NOT a movie strip currently 
            # queued for its own detection, and overlaps with this strip, select it.
            if s.name in originally_selected and s.type != 'MOVIE':
                s_start = get_strip_prop(s, "frame_final_start")
                s_end = get_strip_prop(s, "frame_final_end")
                if not (s_end <= t_start or s_start >= t_end):
                    s.select = True


def check_queue_timer():
    """
    Polled on the main thread to check status of queue workers and dispatch
    the next background job.
    """
    global _queue_state
    if _queue_state is None:
        return None
        
    jobs = _queue_state["jobs"]
    idx = _queue_state["current_index"]
    
    if idx >= len(jobs):
        # All queued video strips processed
        cf = _queue_state["cf"]
        originally_selected = _queue_state["originally_selected"]
        
        bpy.context.scene.frame_current = cf
        bpy.ops.sequencer.select_all(action='DESELECT')
        
        # Reselect whatever remains of the original strips (and their split portions)
        for s in bpy.context.sequencer_scene.sequence_editor.strips_all:
            is_match = False
            for orig_name in originally_selected:
                if s.name == orig_name or s.name.startswith(orig_name + "."):
                    is_match = True
                    break
            if is_match:
                s.select = True
                
        _queue_state = None
        force_vse_redraw()
        
        def draw_finished_popup(self, context):
            self.layout.label(text="All selected video cuts processed successfully!", icon='CHECKMARK')
        bpy.context.window_manager.popup_menu(draw_finished_popup, title="Queue Completed", icon='CHECKMARK')
        return None
        
    current_job = jobs[idx]
    
    if current_job["status"] == "PENDING":
        current_job["status"] = "RUNNING"
        fps = _queue_state["fps"]
        
        thread = threading.Thread(
            target=bg_worker,
            args=(current_job, fps),
            daemon=True
        )
        current_job["thread"] = thread
        thread.start()
        force_vse_redraw()
        return 0.1
        
    if current_job["status"] == "RUNNING":
        return 0.1
        
    status = current_job["status"]
    
    if status == "FAILED":
        print(f"[Detect Shots] Job failed for strip '{current_job['strip_name']}':\n{current_job['error']}")
        _queue_state["current_index"] += 1
        return 0.1
        
    if status == "COMPLETED":
        result_frames = current_job["result"]
        video_start_on_timeline = current_job["video_start_on_timeline"]
        f_start = current_job["f_start"]
        f_end = current_job["f_end"]
        
        if result_frames:
            override = get_vse_context_override()
            if override:
                try:
                    with bpy.context.temp_override(**override):
                        # Configure selection context to contain only this movie strip and target audio siblings
                        prepare_selection_for_job(current_job, _queue_state["originally_selected"])
                        
                        applied_cuts_count = 0
                        for frames in result_frames:
                            cut_frame = int(frames + video_start_on_timeline)
                            if cut_frame > f_start and cut_frame < f_end:
                                bpy.context.scene.frame_current = cut_frame
                                bpy.ops.sequencer.split_selected()
                                applied_cuts_count += 1
                                
                        print(f"[Detect Shots] Spliced '{current_job['strip_name']}' successfully ({applied_cuts_count} cuts added).")
                except Exception as ex:
                    print(f"Exception during split process for '{current_job['strip_name']}': {ex}")
            else:
                print("[Detect Shots] Context override failed: No VSE region found.")
        else:
            print(f"[Detect Shots] No scenes detected in '{current_job['strip_name']}'.")
            
        _queue_state["current_index"] += 1
        force_vse_redraw()
        return 0.1
        
    return 0.1


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
        if context.strips:
            return True
        return False

    def execute(self, context):
        selection = context.selected_strips
        sequences = bpy.context.sequencer_scene.sequence_editor.strips_all
        cf = bpy.context.scene.frame_current
        at_cursor = []
        cut_selected = False

        # find unlocked strips at cursor
        for s in sequences:
            f_start = get_strip_prop(s, "frame_final_start")
            f_end = get_strip_prop(s, "frame_final_end")
            if f_start <= cf and f_end > cf:
                if s.lock == False:
                    at_cursor.append(s)
                    if s.select == True:
                        cut_selected = True
        for s in at_cursor:
            if cut_selected:
                if s.select:  # only cut selected
                    bpy.ops.sequencer.select_all(action="DESELECT")
                    s.select = True
                    bpy.ops.sequencer.split(
                        frame=bpy.context.scene.frame_current,
                        type=self.type,
                        side="RIGHT",
                    )

                    # add new strip to selection
                    for i in bpy.context.sequencer_scene.sequence_editor.strips_all:
                        if i.select:
                            selection.append(i)
                    bpy.ops.sequencer.select_all(action="DESELECT")
                    for s in selection:
                        s.select = True
        return {"FINISHED"}


class SEQUENCER_OT_detect_shots(Operator):
    """Detect shots in all selected movie strips and split accordingly"""

    bl_idname = "sequencer.detect_shots"
    bl_label = "Detect Shots & Split Strips"
    bl_options = {"REGISTER", "UNDO"}

    threshold: FloatProperty(
        name="Threshold",
        description="Threshold for shot detection (lower values are more sensitive)",
        default=32.0,
        min=0.0,
        max=100.0,
    )

    @classmethod
    def poll(cls, context):
        if not (context.scene and context.sequencer_scene.sequence_editor):
            return False
        # Ensures at least one MOVIE strip is selected
        return any(s.type == 'MOVIE' for s in context.selected_strips)

    def execute(self, context):
        global _queue_state
        if _queue_state is not None:
            self.report({'WARNING'}, "Shot detection queue is already active.")
            return {'CANCELLED'}
            
        try:
            check_dependencies()
        except Exception as e:
            self.report({'ERROR'}, f"Verification failed for dependencies: {e}")
            return {'CANCELLED'}
            
        cf = context.scene.frame_current
        render = context.scene.render
        fps = round((render.fps / render.fps_base), 3)
        
        # Capture all originally selected names
        originally_selected = [s.name for s in context.selected_strips]
        
        # Collect only MOVIE strips (this skips SOUND and non-video types)
        movie_strips = [s for s in context.selected_strips if s.type == 'MOVIE']
        
        jobs = []
        for s in movie_strips:
            path = s.filepath
            path = (os.path.realpath(bpy.path.abspath(path))).replace("\\", "\\\\")
            
            start_time = get_strip_prop(s, "frame_offset_start")
            end_time = get_strip_prop(s, "frame_duration") - get_strip_prop(s, "frame_offset_end")
            f_start = get_strip_prop(s, "frame_final_start")
            f_end = get_strip_prop(s, "frame_final_end")
            
            video_start_on_timeline = f_start - start_time
            
            jobs.append({
                "strip_name": s.name,
                "path": path,
                "start_time": start_time,
                "end_time": end_time,
                "f_start": f_start,
                "f_end": f_end,
                "video_start_on_timeline": video_start_on_timeline,
                "threshold": self.threshold,
                "status": "PENDING",
                "result": None,
                "error": None,
                "thread": None,
            })
            
        if not jobs:
            self.report({'WARNING'}, "No eligible movie strips selected.")
            return {'CANCELLED'}
            
        _queue_state = {
            "jobs": jobs,
            "current_index": 0,
            "originally_selected": originally_selected,
            "cf": cf,
            "fps": fps,
        }
        
        bpy.app.timers.register(check_queue_timer)
        force_vse_redraw()
        
        self.report({'INFO'}, f"Started background shot detection for {len(jobs)} movie strip(s)...")
        return {'FINISHED'}


class SEQUENCER_PT_detect_shots(Panel):
    """Panel in the VSE sidebar for adjusting detection settings and triggering the split"""
    bl_label = "Detect Shots"
    bl_idname = "SEQUENCER_PT_detect_shots"
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Strip"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        global _queue_state

        col = layout.column(align=True)
        col.prop(scene, "detect_shots_threshold", text="Threshold", slider=True)
        
        col.separator()
        
        if _queue_state is not None:
            jobs = _queue_state["jobs"]
            idx = _queue_state["current_index"]
            if idx < len(jobs):
                col.label(text=f"Analyzing {idx+1}/{len(jobs)}: {jobs[idx]['strip_name']}", icon='TIME')
            else:
                col.label(text="Completing split passes...", icon='TIME')
        else:
            op = col.operator("sequencer.detect_shots", text="Split Shots")
            op.threshold = scene.detect_shots_threshold


def menu_detect_shots(self, context):
    self.layout.separator()
    op = self.layout.operator("sequencer.detect_shots")
    op.threshold = context.scene.detect_shots_threshold


classes = (
    SEQUENCER_OT_detect_shots,
    SEQUENCER_OT_split_selected,
    SEQUENCER_PT_detect_shots,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.detect_shots_threshold = bpy.props.FloatProperty(
        name="Detect Shots Threshold",
        description="Threshold for shot detection (lower values are more sensitive)",
        default=8.0,
        min=0.0,
        max=100.0,
    )
    bpy.types.SEQUENCER_MT_context_menu.append(menu_detect_shots)
    bpy.types.SEQUENCER_MT_strip.append(menu_detect_shots)


def unregister():
    global _queue_st
    
if __name__ == "__main__":
    register()

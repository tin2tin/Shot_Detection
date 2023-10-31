# Shot Detection in the Blender Video Sequence Editor

[![IMAGE ALT TEXT HERE](https://github.com/tin2tin/shot_detection/blob/main/shot_detect_yt.png?raw=true)](https://www.youtube.com/watch?v=zWNQMII-IAE)

![alt text](https://blender.chat/file-upload/AJKEtutaWrs527Wwv/Cam.gif)

Detected video: 'Caminandes : Llamigos' by Pablo Vazquez, Blender Studio

## Usage
When installed, the operator, "Detect Shots & Split Strips", is exposed in the bottom of the Strip Menu.
The active strip with white outline will be used for detection, and all selected strips will be split at the same points.
Ex. use the movie strip as the active selected strip and the audio strip as selected strip, then both will be split at the same points. 

## Installation
Install the add-on as any Blender add-on.
It is depenadant on this python lib: https://github.com/Breakthrough/PySceneDetect by Brandon Castellano. It should be installed automatically. If not, then this add-on on can be used to install it: https://github.com/amb/blender_pip

## Join Through Splits
Handy add-on for joining strips: https://github.com/tin2tin/join_through_splits

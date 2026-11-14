# Runs on the LAPTOP. Does no analysis: it grabs webcam frames, shrinks them,
# and forwards them to the board, which does all the vision.
#
#   pip install opencv-python "python-socketio[client]"
#   python camera_sender.py http://<board-name>.local:7000

import base64
import sys
import time

import cv2
import socketio

BOARD = sys.argv[1] if len(sys.argv) > 1 else "http://arduino.local:7000"
CAMERA_INDEX = 0        # try 1 if this opens the laptop's built-in camera
FPS = 2

sio = socketio.Client()
sio.connect(BOARD)
cam = cv2.VideoCapture(CAMERA_INDEX)
print("sending frames to", BOARD)

while True:
    ok, frame = cam.read()
    if ok:
        frame = cv2.resize(frame, (320, 240))
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            sio.emit("frame", {"jpg": base64.b64encode(jpg.tobytes()).decode()})
    time.sleep(1.0 / FPS)

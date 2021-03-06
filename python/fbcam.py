import numpy as np
import cv2
import os,sys
import argparse
from time import time
from fbdraw import fb

args=argparse.ArgumentParser()
args.add_argument('-c', '--cv',action='store_true')
args.add_argument('-s', '--shrink',type=int,default=3,choices=[1,2,3])
args.add_argument('-bg','--background',type=str,default='debian2.jpg')
args.add_argument('-cm','--cammode',type=str,default='qvga',choices=['qvga','vga','svga'])
args=args.parse_args()
video_fb = True if args.cv is not True else False
print(video_fb)

if video_fb:
    fb0 = fb(shrink=args.shrink)
    fbB = fb(shrink=1)
    assert os.path.exists(args.background)
    background = cv2.imread(args.background)
    os.system('clear')
    if os.system('which clear') == 0: os.system('clear')
    fbB.imshow('back',background)
    print "Hitachi Solutions Technology"
    if os.system('which setterm') == 0: os.system('setterm -blank 0;echo setterm -blank 0')
    fbB = fb(shrink=1)
    fbB.close()
    print("virtual_size:",fb0.vw,fb0.vh)
cap = cv2.VideoCapture(0)
print("cam.property-default:",cap.get(3),cap.get(4))
if args.cammode=='vga':
    cap.set(3,640)  # 3:width
    cap.set(4,480)  # 4:height
elif args.cammode=='svga':
    cap.set(3,800)  # 3:width
    cap.set(4,600)  # 4:height
elif args.cammode=='qvga':
    cap.set(3,320)  # 3:width
    cap.set(4,240)  # 4:height
print("cam.property-set:",cap.get(3),cap.get(4),args.cammode)
print("shrink:1/%d"%args.shrink)

cnt = 0
start = time()
while(cap.isOpened()):
    ret, frame = cap.read()
    frame = cv2.flip(frame,0)
    if ret==True:
        frame = cv2.flip(frame,0)

        if video_fb is True :fb0.imshow('frame',frame)
        if video_fb is False:cv2.imshow('frame',frame)
        cnt+=1
        elapsed = time() - start
        sys.stdout.write('\b'*30)
        sys.stdout.write("%.3fFPS"%(cnt/elapsed))
        sys.stdout.flush()
        if video_fb is False and cv2.waitKey(1) != -1:break
    else:
        break

# Release everything if job is finished
if video_fb: fb0.close()
cap.release()
cv2.destroyAllWindows()

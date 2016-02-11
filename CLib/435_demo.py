import os,sys,re,argparse
import glob,random,threading
import numpy as np
from   devmemX import devmem
import cv2
from time import sleep,time
from fbdraw import fb
from multiprocessing import Process, Queue
from   pdb import *
import dn
import pyrealsense2 as rs
from random import randrange, seed


args=argparse.ArgumentParser()
args.add_argument('-c', '--cv',action='store_true')
args.add_argument('-s', '--shrink',type=int,default=2,choices=[1,2,3])
args.add_argument('-bg','--background',type=str,default='debian2.jpg')
args.add_argument('-k','--keep',type=int,default=600)
args.add_argument('-vn','--videoNo',type=int,default=0)
args.add_argument('-th','--thread',action='store_true')
args.add_argument('-dma',action='store_true')
args.add_argument('--debug_log',action='store_true')
args.add_argument('-phys','--phys_addr',type=str,default='/sys/class/udmabuf/udmabuf0/phys_addr')
args.add_argument('-cm','--cammode',type=str,default='qvga',choices=['qvga','vga','svga'])
args.add_argument('--cam_h',type=int,default=640)
args.add_argument('--cam_w',type=int,default=320)
args.add_argument( '-d','--depth',   action='store_true')
args=args.parse_args()

seed(22222)
assert os.path.exists('/dev/fb0') and os.path.exists('/dev/video'+str(args.videoNo))
ph_height = 288 # placeholder height
ph_width  = 352 # placeholder width
ph_chann  = 3

def backgrounder(image_path, args):
    if os.system('which clear') == 0: os.system('clear')
    fbB = fb(shrink=1)
    assert os.path.exists(image_path)
    background = cv2.imread(image_path)
    fbB.imshow('back',background)
    fbB.close()
    os.system("figlet HST")
    print("virtual_size:",fb0.vw,fb0.vh)
    print("/dev/video* :",args.video_devs)
    print("camra :",args.cam_w, args.cam_h, "shrink:1/%d"%args.shrink, "thread:", args.thread, "DMA Mode:", args.dma)

fb0 = fb(shrink=args.shrink)
if os.system('which setterm') == 0: os.system('setterm -blank 0;echo setterm -blank 0')

image_area_addr = 0xe018c000
if args.dma and os.path.exists(args.phys_addr):
    with open(args.phys_addr) as f:
        cmd = "image_area_addr = %s"%(f.read().strip())
    exec(cmd)
else:
    args.dma=False

print("image_area_addr:%x"%image_area_addr)
devmem_image = devmem(image_area_addr, ph_height*ph_width*ph_chann)
devmem_start = devmem(0xe0c00004,4)
devmem_stat  = devmem(0xe0c00008,0x4)
devmem_pred  = devmem(0xe0000000,0xc15c)
devmem_dmac  = devmem(0xe0c00018,4)
devmem_pfmc  = devmem(0xe0c00020,4)
if args.dma:
    print("DMA-Mode:On")
    c = np.asarray([0x00000000],dtype=np.uint32).tostring()
    b = np.asarray([image_area_addr],dtype=np.uint32).tostring()
    devmem_dmab  = devmem(0xe0c00010,4)
    devmem_dmab.write(b)
    devmem_dmab.close()
else:
    print("DMA-Mode:Off")
    c = np.asarray([0x80000000],dtype=np.uint32).tostring()
devmem_dmac.write(c)
devmem_dmac.close()

n_classes = 20
grid_h    =  9
grid_w    = 11
box_coord =  4
n_b_boxes =  5
n_info_per_grid = box_coord + 1 + n_classes

classes = [
    "aeroplane", "bicycle", "bird", "boat", "bottle",
    "bus", "car", "cat", "chair", "cow",
    "diningtable", "dog", "horse", "motorbike", "person",
    "pottedplant", "sheep", "sofa", "train", "tvmonitor"
]
colors = [(254.0, 254.0, 254), (239.8, 211.6, 127), 
          (225.7, 169.3, 0), (211.6, 127.0, 254),
          (197.5, 84.6, 127), (183.4, 42.3, 0),
          (169.3, 0.0, 254), (155.2, -42.3, 127),
          (141.1, -84.6, 0), (127.0, 254.0, 254), 
          (112.8, 211.6, 127), (98.7, 169.3, 0),
          (56.4, 42.3, 0), (42.3, 0.0, 254), 
          (84.6, 127.0, 254), (70.5, 84.6, 127),
          (28.2, -42.3, 127), (14.1, -84.6, 0),
          (0.0, 254.0, 254), (-14.1, 211.6, 127)]

# YOLOv2 anchor of Bounding-Boxes
anchors = [1.08,1.19,  3.42,4.41,  6.63,11.38,  9.42,5.11,  16.62,10.52]

class D435:
    def __init__(self, qi, color=True, depth=False, w=640, h=480, fps=30, warmup=30):
        self.qi       = qi
        self.color    = color
        self.depth    = depth
        self.w        = w
        self.h        = h
        self.pipeline = rs.pipeline()
        config        = rs.config()
      #  self.align_to = rs.stream.color
      #  self.align    = rs.align(self.align_to)
        config.enable_stream(rs.stream.color, w, h, rs.format.bgr8, fps)
        profile = self.pipeline.start(config)
        self.scale    = profile.get_device().first_depth_sensor().get_depth_scale()
        self.hwc_array= self.depth_frame = self.depth_array = None
        self.rea_time = self.pre_time = 0
        self.warmup   = warmup+1
        print("depth_sensor_scale:",self.scale)
        self.warm_up()
        if depth:
            self.release()
            self.pipeline = rs.pipeline()
            config        = rs.config()
            self.align_to = rs.stream.color
            self.align    = rs.align(self.align_to)
            config.enable_stream(rs.stream.depth, w, h, rs.format.z16,  fps)
            profile       = self.pipeline.start(config)
            self.scale    = profile.get_device().first_depth_sensor().get_depth_scale()
            if not self.warm_up():sys.exit(-1)
            self.release()
            self.pipeline = rs.pipeline()
            config        = rs.config()
            self.align_to = rs.stream.color
            self.align    = rs.align(self.align_to)
            if color: config.enable_stream(rs.stream.color, w, h, rs.format.bgr8, fps)
            if depth: config.enable_stream(rs.stream.depth, w, h, rs.format.z16,  fps)
            profile       = self.pipeline.start(config)
            self.scale    = profile.get_device().first_depth_sensor().get_depth_scale()
            if not self.warm_up():sys.exit(-1)

    def warm_up(self):
        print("warm up phase1 ...")
        sleep(3)
        print("warm up phase2 ...")
        up = False
        for j in range(5):
            try:
                for i in range(self.warmup):
                    sys.stdout.write('\b'*30)
                    sys.stdout.write('%d:%10d/%d'%(j,i,self.warmup))
                    sys.stdout.flush()
                    frames = self.pipeline.wait_for_frames()
                    #sleep(0.2)
            except:
                sleep(1)
                continue
            else:
                up = True
                break
        if up:
            print("\nup")
            return True
        else:
            #self.release()
            return False

    def prep_Qi(self):
        pre_start= time()
        preprocessed_nchwRGB = preprocessing(self.hwc_array, 288, 352)
        if self.qi.full(): self.qi.get()
        self.qi.put([preprocessed_nchwRGB, self.depth_array])
        self.pre_time = time() - pre_start

    def read(self):
        rea_start = time()
        frames = self.pipeline.wait_for_frames()
        if self.color:
            color_frame    = frames.get_color_frame()   # no needs align.process for color stream
            self.hwc_array = np.asanyarray(color_frame.get_data())
        if self.depth:
            align_frame      = self.align.process(frames)
            self.depth_frame = align_frame.get_depth_frame()
            depth_frame1     = self.depth_frame.as_depth_frame()
            self.depth_array = np.asanyarray(self.depth_frame.get_data())
        self.prep_Qi()
        self.rea_time = time() - rea_start
        return self.hwc_array, self.depth_frame, self.depth_array, self.rea_time, self.pre_time, self.rea_time + self.pre_time

    def release(self):
        self.pipeline.stop()

class UVC:
    def __init__(self, qi, deviceNo=0, cammode='vga'):
        self.qi = qi
        assert os.path.exists('/dev/video'+str(deviceNo))
        cap = self.cap = cv2.VideoCapture(deviceNo)
        assert self.cap is not None
        print("cam.property-default:",cap.get(3),cap.get(4))
        if cammode=='vga':
            cap.set(3,640)  # 3:width
            cap.set(4,480)  # 4:height
        elif cammode=='svga':
            cap.set(3,800)  # 3:width
            cap.set(4,600)  # 4:height
        elif cammode=='qvga':
            cap.set(3,320)  # 3:width
            cap.set(4,240)  # 4:height
        print("cam.property-set:",cap.get(3),cap.get(4),cammode)
        self.w = cap.get(3)
        self.h = cap.get(4)
        self.r,self.frame = self.cap.read()
        assert self.r is True
        self.cont  = True
        self.thread= None
        self.rea_time = 0
        self.pre_time = 0

    def read_image(self):
        rea_start = time()
        r,self.frame = self.cap.read()
        assert r is True
        self.rea_time= time() - rea_start
        return r, self.frame

    def prep_Qi(self):
        pre_start= time()
        preprocessed_nchwRGB = preprocessing(self.frame, 288, 352)
        if self.qi.full(): self.qi.get()
        self.qi.put(preprocessed_nchwRGB)
        self.pre_time = time() - pre_start

    def _read_task(self):
        while True:
            if not self.cont:break
            r, self.frame = self.read_image()
            self.prep_Qi()
        self.cap.release()

    def start(self):
        self.thread = threading.Thread(target=self._read_task,args=())
        self.thread.start()
        return self

    def stop(self):
        self.cont=False
        self.thread.join()

    def read(self):
        if self.thread is None:
            r,self.frame = self.read_image()
            assert r is True
        else:
            self.r, self.frame = self.read_image()
        self.prep_Qi()
        return self.r, self.frame, self.rea_time, self.pre_time, self.rea_time + self.pre_time

def box2rect(box):
    x, y, h, w = box
    lx, ly, rx, ry = x-w/2., y-h/2., x+w/2., y+h/2.
    if lx < 0: lx =0.
    if ly < 0: ly =0.
    return [int(lx), int(ly), int(rx), int(ry)]

def preprocessing(input_image,ph_height,ph_width):

  resized_image  = cv2.resize(input_image,(ph_width, ph_height))
  resized_image  = cv2.cvtColor(resized_image,cv2.COLOR_BGR2RGB)
  resized_chwRGB = resized_image.transpose((2,0,1))  # CHW RGB
  #resized_chwRGB /= 255.
  image_nchwRGB  = np.expand_dims(resized_chwRGB, 0) # NCHW RGB
  return image_nchwRGB

class Core:
    def __init__(self):
        self.dma_full = 1

    def fpga(self, preprocessed_nchwRGB, ph_height, ph_width,devmem_image, devmem_start, devmem_stat, devmem_pred):
        start = time()
        d = preprocessed_nchwRGB.reshape(-1).astype(np.uint8).tostring()
        devmem_image.write(d)
        devmem_image.rewind()

        s = np.asarray([0x1],dtype=np.uint32).tostring()
        devmem_start.write(s)
        devmem_start.rewind()
        wrt = time() - start
        start = time()
        sleep(0.005)
        for i in range(10000):
            status = devmem_stat.read(np.uint32)
            devmem_stat.rewind()
            if status[0] == 0x2000:
                break
            sleep(0.001)
        exe = time() - start
        start = time()

    # Compute the predictions on the input image
        #predictions = devmem_pred.read(np.float32)
        predictions = dn.get_predictions()
        #devmem_pred.rewind()
        assert predictions[0]==predictions[0],"invalid mem values:{}".format(predictions[:8])
    #   _predictions________________________________________________________
    #   | 4 entries                 |1 entry |     20 entries               |
    #   | x..x | y..y | w..w | h..h | c .. c | p0 - p19      ..     p0 - p19| x 5(==num)
    #   ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    #   entiry size == grid_w x grid_h

        ret = time() - start
        return predictions, wrt, exe, ret

    def fpga_dma(self, preprocessed_nchwRGB, ph_height, ph_width,devmem_image, devmem_start, devmem_stat, devmem_pred):

        start = time()
        s = np.asarray([0x1],dtype=np.uint32).tostring()
        devmem_start.write(s)
        devmem_start.rewind()
        sleep(0.001)

        w_start = time()
        for i in range(10000):
            status = devmem_stat.read(np.uint32)
            devmem_stat.rewind()
            if status[0] != 0x13000:    # DMA Idle
                break
        d = preprocessed_nchwRGB.reshape(-1).astype(np.uint8).tostring()
        devmem_image.write(d)
        devmem_image.rewind()               # write to DMA area
        wrt = time() - w_start

        for i in range(10000):
            status = devmem_stat.read(np.uint32)
            devmem_stat.rewind()
            if status[0] == 0x2000:     # CNN Idle
                break
            sleep(0.001)
        exe = time() - start

        start = time()
    # Compute the predictions on the input image
        #predictions = devmem_pred.read(np.float32)
        predictions = dn.get_predictions()
        #devmem_pred.rewind()
        assert predictions[0]==predictions[0],"invalid mem values:{}".format(predictions[:8])
    #   _predictions________________________________________________________
    #   | 4 entries                 |1 entry |     20 entries               |
    #   | x..x | y..y | w..w | h..h | c .. c | p0 - p19      ..     p0 - p19| x 5(==num)
    #   ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    #   entiry size == grid_w x grid_h

        ret = time() - start
        return predictions, wrt, exe, ret

def fpga_proc(qi, qp, qs, ph_height, ph_width,devmem_image, devmem_start, devmem_stat, devmem_pred, devmem_pfmc):
    print 'start fpga processing'
    dn.open_predictions(0xe0000000,11*9*125)
    core = Core()
    infers = 0
    start = time()
    while True:
        infers += 1
        preprocessed_nchwRGB, dth_array = qi.get()
        if args.dma:
            latest, wrt, exe, ret = core.fpga_dma(
                preprocessed_nchwRGB, ph_height, ph_width,devmem_image, devmem_start, devmem_stat, devmem_pred)
        else:
            latest, wrt, exe, ret = core.fpga(
                preprocessed_nchwRGB, ph_height, ph_width,devmem_image, devmem_start, devmem_stat, devmem_pred)
        if latest is not None:
            if qp.full(): qp.get()
            qp.put([latest, dth_array])
        if qs.full(): qs.get()
        qs.put([wrt,exe,ret,(time()-start)/infers])
    dn.close_predictions()

QUEUE_SIZE=3
def main(args):
    me_dir = os.path.dirname(os.path.abspath(__file__))

    # Definition of the parameters
    score_threshold = 0.3
    iou_threshold = 0.3

    qi = Queue(QUEUE_SIZE)
    qp = Queue(QUEUE_SIZE)
    qs = Queue(QUEUE_SIZE)

    video_devs = len(glob.glob('/dev/video*'))
    if video_devs==1:
        cap = UVC(qi, deviceNo=args.videoNo, cammode=args.cammode)
    else:
        args.cam_w = 640
        args.cam_h = 480
        cap = D435(qi, color=True, depth=args.depth)
    args.video_devs = video_devs

    sum_cam = objects = images = 0
    colapse = 0
    verbose=False
    fp = Process(target=fpga_proc, args=(qi, qp, qs, ph_height, ph_width, devmem_image, devmem_start, devmem_stat, devmem_pred, devmem_pfmc,))
    latest_res=[]
    fp.start()
    start = time()
    fpga_total = wrt_stage = exe_stage = ret_stage = loo_stage = 1.
    backgrounder(args.background, args)
    mask = None
    seg_colors = [[r, 100+i, randrange(100,180)] for i,r in enumerate(range(180,180-20,-1))]
    while True:
        cap_start = time()
        if video_devs == 1:
            r,frame,rea_time, pre_time, cam_time   = cap.read()
        else:
            frame, dth, dth_np, rea_time, pre_time, cam_time = cap.read()
        if mask is None: mask = np.zeros((480,640,3), dtype=np.uint8)
        #if mask is None: mask = np.zeros(frame.shape, dtype=np.uint8)
        cap_time = time() - cap_start
        images  += 1
        pos_start= time()

        try:
            predictions, _= qp.get_nowait()
            im_h, im_w = frame.shape[:2]
            res = dn.postprocessing(predictions,im_w,im_h,0.5,0.5)
            objects = len(res)
            latest_res = res
        except:
            pass

        try:
            wrt_stage, exe_stage, ret_stage, loo_stage = qs.get_nowait()
        except:
            pass

        mask = (mask / 2).astype(np.uint8)
        for r in latest_res:
            name, conf, bbox = r[:3]
            obj_col = colors[classes.index(r[0])]
            seg_col = seg_colors[classes.index(r[0])]
            rect = box2rect(bbox)   # x1, y1, x2, y2 := left_top right_bottom
            if args.depth:
                
                dth_obj_m= dth_np[rect[1]:rect[3], rect[0]:rect[2]]*cap.scale  # y1:y2, x1:x2 area
                dth_obj_m = np.clip(dth_obj_m, 0.001, 10.000) # histogram of meter wise until 20m
                bins, range_m = np.histogram(dth_obj_m, bins=10)
                index_floor = np.argmax(bins)                 # range which occupy most area in bbox
                range_floor = range_m[index_floor]
                range_ceil  = range_m[index_floor+1]
                indexYX = np.where((dth_obj_m>range_floor) & (dth_obj_m<range_ceil))
                if len(indexYX[0]) == 0 and len(indexYX[1]) == 0:continue   # indexYX := y,x
                meters  = dth_obj_m[indexYX].min()
                indexYX = (indexYX[0]+rect[1], indexYX[1]+rect[0])          # y+=y1, x+=x1
                mask[indexYX[0], indexYX[1], :] = seg_col
                name = "%s(%.2fm)"%(name,meters)

            cv2.rectangle(
                mask,
                ( rect[0], rect[1] ),
                ( rect[2], rect[3] ),
                obj_col
            )
            cv2.putText(
                mask,
                name,
                (int(bbox[0]), int(bbox[1])),
                cv2.FONT_HERSHEY_SIMPLEX,1,
                obj_col,
                2)
        pos_time = time() - pos_start
        scr_start= time()
        colapse = time()-start
        if (int(colapse)%args.keep)==0:
            image_path = random.choice(glob.glob(os.path.join(me_dir,'debian*.jpg')))
            backgrounder(image_path, args)
            sleep(1.0)
        fb0.imshow('result', frame | mask)
        if args.dma:
            stages = exe_stage + ret_stage
        else:
            stages = wrt_stage + exe_stage + ret_stage
        if stages == 0.: stages=1.
        scr_time = time() - scr_start
        sys.stdout.write('\b'*100)
        if args.debug_log:
            msg=('FPGA/CAM: %7.1fFPS(%5.1f%5.1f%5.1f) PLAY:%5.1fFPS(%5.1f:%5.1f%5.1f%5.1f%5.1f) %d objects'%(
            1./(stages), 1000.*wrt_stage, 1000.*exe_stage, 1000.*ret_stage,
            images/colapse, 1000.*colapse/images, 1000.*cap_time, 1000.*pre_time, 1000.*pos_time, 1000.*scr_time, objects
            ))
            msg = str(msg)[:88]
        else:
            sum_cam += cam_time
            msg=('CAMERA: %7.1fFPS  FPGA:%7.1fmsec  PLAYBACK:%7.1fFPS %d objects'%(
            images/sum_cam, 1000.*stages, 1./(time() - cap_start), objects
 #           1./cam_time, 1000.*stages, 1./(time() - cap_start), objects
            ))
            msg = str(msg)[:88]
        sys.stdout.write(msg)
        sys.stdout.flush()

if __name__ == '__main__':
     main(args) 


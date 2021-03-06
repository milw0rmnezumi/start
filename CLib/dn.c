#include <stdio.h>
#include <malloc.h>
#include <string.h>
#include <math.h>
#include <float.h>
#include <stdlib.h>

#include <unistd.h>
#include <errno.h>
#include <signal.h>
#include <fcntl.h>
#include <ctype.h>
#include <termios.h>
#include <sys/types.h>
#include <sys/mman.h>
  
typedef struct{
    float x, y, w, h;
} box;

typedef struct candidate{
    int   class;
    float prob;
    box   bbox;
} candidate;

typedef struct detection{
    box bbox;
    int classes;
    float *prob;
    float *mask;
    float objectness;
    int sort_class;
} detection;

typedef struct m_layer{
    int outputs;
    float *output;
    float *mean_output;
    float moving_alpha;
    float *biases;
    int batch;
    int softmax;
    int softmax_tree;
    int w,h,n;
    int coords,classes;
    int inputs;
    int background;
} m_layer;
int entry_index(m_layer l, int batch, int location, int entry)
{
    int n =   location / (l.w*l.h);
    int loc = location % (l.w*l.h);
    return batch*l.outputs + n*l.w*l.h*(l.coords+l.classes+1) + entry*l.w*l.h + loc;
}

static inline float logistic_activate(float x){return 1./(1. + exp(-x));}
void activate_array(float *x, const int n)
{
    int i;
    for(i = 0; i < n; ++i){
        x[i] = logistic_activate(x[i]);
    }
}

void softmax(float *input, int n, float temp, int stride, float *output)
{
    int i;
    float sum = 0;
    float largest = -FLT_MAX;
    for(i = 0; i < n; ++i){
        if(input[i*stride] > largest) largest = input[i*stride];
    }
    for(i = 0; i < n; ++i){
        float e = exp(input[i*stride]/temp - largest/temp);
        sum += e;
        output[i*stride] = e;
    }
    for(i = 0; i < n; ++i){
        output[i*stride] /= sum;
    }
}

void softmax_cpu(float *input, int n, int batch, int batch_offset, int groups, int group_offset, int stride, float temp, float *output)
{
    int g, b;
    for(b = 0; b < batch; ++b){
        for(g = 0; g < groups; ++g){
            softmax(input + b*batch_offset + g*group_offset, n, temp, stride, output + b*batch_offset + g*group_offset);
        }
    }
}

void forward_region_layer(m_layer l)
{
    int i,j,b,t,n;
    //memcpy(l.output, net.input, l.outputs*l.batch*sizeof(float));
    float *net_input = calloc(l.outputs, sizeof(float));
    memcpy(net_input, l.output, l.outputs*l.batch*sizeof(float));

    for (b = 0; b < l.batch; ++b){
        for(n = 0; n < l.n; ++n){
            int index = entry_index(l, b, n*l.w*l.h, 0);
            activate_array(l.output + index, 2*l.w*l.h);
            index = entry_index(l, b, n*l.w*l.h, l.coords);
            if(!l.background) activate_array(l.output + index, l.w*l.h);
            index = entry_index(l, b, n*l.w*l.h, l.coords + 1);
            if(!l.softmax && !l.softmax_tree) activate_array(l.output + index, l.classes*l.w*l.h);
        }
    }
    {
        int index = entry_index(l, 0, 0, l.coords + !l.background);
        softmax_cpu(net_input + index, l.classes + l.background, l.batch*l.n, l.inputs/l.n, l.w*l.h, 1, l.w*l.h, 1, l.output + index);
    }
    free(net_input);
}
//gdb:correct_region_boxes (dets=0x145be30, n=495, w=768, h=576, netw=352, neth=288, relative=1)
void correct_region_boxes(detection *dets, int n, int w, int h, int netw, int neth, int relative)
{
    int i;
    int new_w=0;
    int new_h=0;
    if (((float)netw/w) < ((float)neth/h)) {
        new_w = netw;
        new_h = (h * netw)/w;
    } else {
        new_h = neth;
        new_w = (w * neth)/h;
    }
    for (i = 0; i < n; ++i){
        box b = dets[i].bbox;
        b.x =  (b.x - (netw - new_w)/2./netw) / ((float)new_w/netw); 
        b.y =  (b.y - (neth - new_h)/2./neth) / ((float)new_h/neth); 
        b.w *= (float)netw/new_w;
        b.h *= (float)neth/new_h;
        if(!relative){
            b.x *= w;
            b.w *= w;
            b.y *= h;
            b.h *= h;
        }
        dets[i].bbox = b;
    }
}

box get_region_box(float *x, float *biases, int n, int index, int i, int j, int w, int h, int stride)
{
    box b;
    b.x = (i + x[index + 0*stride]) / w;
    b.y = (j + x[index + 1*stride]) / h;
    b.w = exp(x[index + 2*stride]) * biases[2*n]   / w;
    b.h = exp(x[index + 3*stride]) * biases[2*n+1] / h;
    return b;
}

//gdb:get_region_detections (l=..., w=768, h=576, netw=352, neth=288, thresh=0.5, map=0x0, tree_thresh=0.5, relative=1, dets=0x145be30)
void get_region_detections(m_layer l, int w, int h, int netw, int neth, float thresh, int *map, float tree_thresh, int relative, detection *dets)
{
    int i,j,n,z;
    float *predictions = l.output;
    float *mean_pred   = l.mean_output;
    for (i = 0; i < l.w*l.h; ++i){
        int row = i / l.w;
        int col = i % l.w;
        for(n = 0; n < l.n; ++n){
            int index = n*l.w*l.h + i;
            for(j = 0; j < l.classes; ++j){
                dets[index].prob[j] = 0;
            }
            int obj_index  = entry_index(l, 0, n*l.w*l.h + i, l.coords);
            int box_index  = entry_index(l, 0, n*l.w*l.h + i, 0);
            int mask_index = entry_index(l, 0, n*l.w*l.h + i, 4);
            float scale = l.background ? 1 : predictions[obj_index];
            float diffm = scale - mean_pred[obj_index];
            if (diffm<0){
                mean_pred[obj_index] +=l.moving_alpha * diffm;
                scale = mean_pred[obj_index];
            }else
                mean_pred[obj_index] = scale;
            dets[index].bbox = get_region_box(predictions, l.biases, n, box_index, col, row, l.w, l.h, l.w*l.h);
            dets[index].objectness = scale > thresh ? scale : 0;
            if(dets[index].mask){
                for(j = 0; j < l.coords - 4; ++j){
                    dets[index].mask[j] = l.output[mask_index + j*l.w*l.h];
                }
            }

            int class_index = entry_index(l, 0, n*l.w*l.h + i, l.coords + !l.background);
            if(dets[index].objectness){
                for(j = 0; j < l.classes; ++j){
                    int class_index = entry_index(l, 0, n*l.w*l.h + i, l.coords + 1 + j);
                    float prob = scale*predictions[class_index];
                    dets[index].prob[j] = (prob > thresh) ? prob : 0;
                }
            }
        }
    }
    correct_region_boxes(dets, l.w*l.h*l.n, w, h, netw, neth, relative);
}

//gdb:fill_network_boxes (net=0x897de0, w=768, h=576, thresh=0.5, hier=0.5, map=0x0, relative=1, dets=0x145be30)
void fill_network_boxes(m_layer *l_p, int w, int h, float thresh, float hier, int *map, int relative, detection *dets)
{
    int j;
    m_layer l = *l_p;
    //get_region_detections(l, w, h, net->w, net->h, thresh, map, hier, relative, dets);
    get_region_detections(l, w, h, l_p->w, l_p->h, thresh, map, hier, relative, dets);
}
/*
//gdb:num_detections (net=0x897de0, thresh=0.5)
int num_detections(network *net, float thresh)
*/

detection *make_network_boxes(m_layer *l_p, float thresh, int *num)
{
    //layer l = net->layers[net->n - 1];
    int i;
    //gdb:  495  = num_detections (net=0x897de0, thresh=0.5)
    //int nboxes = num_detections(net, thresh);
    m_layer l=*l_p;
    int nboxes = l.w*l.h*l.n;
    if(num) *num = nboxes;
    detection *dets = calloc(nboxes, sizeof(detection));
    for(i = 0; i < nboxes; ++i){
        dets[i].prob = calloc(l.classes, sizeof(float));
        if(l.coords > 4){
            dets[i].mask = calloc(l.coords-4, sizeof(float));
        }
    }
    return dets;
}
detection *get_network_boxes(m_layer *l_p, int w, int h, float thresh, float hier, int *map, int relative, int *num)
{
    int i;
    //gdb:make_network_boxes (net=0x897de0, thresh=0.5, num=0x7fffffffdcbc)
    detection *dets = make_network_boxes(l_p, thresh, num);
    fill_network_boxes(l_p, w, h, thresh, hier, map, relative, dets);
    return dets;
}

int candidate_comparator(const void *a, const void *b){
    const candidate *_a=a;
    const candidate *_b=b;
    if     (_a->prob > _b->prob) return -1;
    else if(_a->prob < _b->prob) return  1;
    else                         return  0;
}

candidate *get_candidates(detection *dets, int n, int classes, int *outs)
{
    int i,j;
    candidate *cand=calloc(n, sizeof(candidate));
    for(j=0;j<n;j++)
        for(i=0;i<classes;i++){
            if(dets[j].prob[i]>0.0){
                cand[*outs].class = i;
                cand[*outs].prob  = dets[j].prob[i];
                cand[*outs].bbox  = dets[j].bbox;
                (*outs)++;
            }
        }
    qsort(cand, *outs, sizeof(candidate),candidate_comparator);
    return cand;
}

float overlap(float x1, float w1, float x2, float w2)
{
    float l1 = x1 - w1/2;
    float l2 = x2 - w2/2;
    float left = l1 > l2 ? l1 : l2;
    float r1 = x1 + w1/2;
    float r2 = x2 + w2/2;
    float right = r1 < r2 ? r1 : r2;
    return right - left;
}

float box_intersection(box a, box b)
{
    float w = overlap(a.x, a.w, b.x, b.w);
    float h = overlap(a.y, a.h, b.y, b.h);
    if(w < 0 || h < 0) return 0;
    float area = w*h;
    return area;
}

float box_union(box a, box b)
{
    float i = box_intersection(a, b);
    float u = a.w*a.h + b.w*b.h - i;
    return u;
}

float box_iou(box a, box b)
{
    return box_intersection(a, b)/box_union(a, b);
}

int nms_comparator(const void *pa, const void *pb)
{
    detection a = *(detection *)pa;
    detection b = *(detection *)pb;
    float diff = 0;
    if(b.sort_class >= 0){
        diff = a.prob[b.sort_class] - b.prob[b.sort_class];
    } else {
        diff = a.objectness - b.objectness;
    }
    if(diff < 0) return 1;
    else if(diff > 0) return -1;
    return 0;
}

void do_nms_obj(detection *dets, int total, int classes, float thresh)
{
    int i, j, k;
    k = total-1;
    for(i = 0; i <= k; ++i){
        if(dets[i].objectness == 0){
            detection swap = dets[i];
            dets[i] = dets[k];
            dets[k] = swap;
            --k;
            --i;
        }
    }
    total = k+1;

    for(i = 0; i < total; ++i){
        dets[i].sort_class = -1;
    }

    qsort(dets, total, sizeof(detection), nms_comparator);
    for(i = 0; i < total; ++i){
        if(dets[i].objectness == 0) continue;
        box a = dets[i].bbox;
        for(j = i+1; j < total; ++j){
            if(dets[j].objectness == 0) continue;
            box b = dets[j].bbox;
            if (box_iou(a, b) > thresh){
                dets[j].objectness = 0;
                for(k = 0; k < classes; ++k){
                    dets[j].prob[k] = 0;
                }
            }
        }
    }
}

void free_detections(detection *dets, int n)
{
    int i;
    for(i = 0; i < n; ++i){
        free(dets[i].prob);
        if(dets[i].mask) free(dets[i].mask);
    }
    free(dets);
}

void free_any(void *ptr){free(ptr);}

#define FATAL do { fprintf(stderr, "Error at line %d, file %s (%d) [%s]\n", \
  __LINE__, __FILE__, errno, strerror(errno)); exit(1); } while(0)
 
#define MAP_SIZE 4096UL
#define MAP_MASK (MAP_SIZE - 1)

#define READ_TYPE float
static void *map_base, *virt_addr; 
static READ_TYPE *req_buf;
static int mem_fd;
static size_t req_words;

void open_predictions(size_t target, size_t req_words_) {
    int i;
	unsigned long read_result, writeval;
    req_words = req_words_;
    req_buf=(READ_TYPE *)calloc(req_words,sizeof(float));
	
    if((mem_fd = open("/dev/mem", O_RDWR | O_SYNC)) == -1) FATAL;
    printf("/dev/mem opened.\n"); fflush(stdout);
    
    /* Map one page */
    size_t map_size = (req_words*sizeof(READ_TYPE)/MAP_SIZE + 1)*MAP_SIZE;
    map_base = mmap(0, map_size, PROT_READ | PROT_WRITE, MAP_SHARED, mem_fd, target & ~MAP_MASK);
    if(map_base == (void *) -1) FATAL;
    printf("Memory mapped at address %p.\n", map_base); fflush(stdout);

    virt_addr = map_base + (target & MAP_MASK);
}

void read_predictions(READ_TYPE *req_buf) {
    register int i;
    for(i=0;i<req_words;i++){
        req_buf[i] = *((READ_TYPE *)virt_addr+i);
    }
    lseek(mem_fd,0,SEEK_SET);
}

void close_predictions(){
    printf("/dev/mem closed.\n"); fflush(stdout);
	if(munmap(map_base, MAP_SIZE) == -1) FATAL;
    close(mem_fd);
    free(req_buf);
}


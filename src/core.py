"""Geometric primitives for single-frame (photo) analysis."""
import numpy as np


def calculate_angle(a, b, c):
    a, b, c = np.array(a), np.array(b), np.array(c)
    radians = np.arctan2(c[1] - b[1], c[0] - b[0]) - np.arctan2(a[1] - b[1], a[0] - b[0])
    angle = np.abs(radians * 180.0 / np.pi)
    if angle > 180.0: angle = 360 - angle
    return angle

def calculate_angle_horizontal(a, b):
    """Acute angle (0-90 deg) between segment a->b and the horizontal.

    Folding into 0-90 makes the metric independent of which way the rider faces
    (a left-facing rider would otherwise report ~140 deg instead of ~40 deg).
    """
    v = np.array(b, dtype=float) - np.array(a, dtype=float)
    ang = np.abs(np.degrees(np.arctan2(v[1], v[0])))
    return float(min(ang, 180.0 - ang))


def detect_side(lm_dict):
    nose = lm_dict.get('nose')
    l_ear = lm_dict.get('left_ear')
    r_ear = lm_dict.get('right_ear')
    
    if nose is not None and l_ear is not None:
        if nose[0] < l_ear[0]: return 'left'
        else: return 'right'
    if nose is not None and r_ear is not None:
        if nose[0] < r_ear[0]: return 'left'
        else: return 'right'
        
    l_hip = lm_dict.get('left_hip')
    l_knee = lm_dict.get('left_knee')
    if l_hip is not None and l_knee is not None:
        if l_knee[0] < l_hip[0]: return 'left'
        else: return 'right'
        
    return 'left'

def get_primary_landmarks(lm_dict, facing_side):
    left_keys = ['left_shoulder', 'left_hip', 'left_knee', 'left_ankle', 'left_heel', 'left_toe']
    right_keys = ['right_shoulder', 'right_hip', 'right_knee', 'right_ankle', 'right_heel', 'right_toe']
    
    l_count = sum(1 for k in left_keys if k in lm_dict)
    r_count = sum(1 for k in right_keys if k in lm_dict)
    
    # A rider facing image-left shows their LEFT side to the camera (and vice
    # versa), so prefer the facing side whenever enough of it is visible.
    if facing_side == 'left' and l_count >= 4:
        prefix = 'left_'
    elif facing_side == 'right' and r_count >= 4:
        prefix = 'right_'
    else:
        prefix = 'left_' if l_count >= r_count else 'right_'
    
    unified = {}
    for k, v in lm_dict.items():
        if k.startswith(prefix):
            unified[k.replace(prefix, '')] = v
        unified[k] = v 
    unified['side'] = prefix.replace('_', '')
    return unified

def analyze_posture(lm):
    angles = {}
    def valid(pt): return pt is not None and not (pt[0] == 0 and pt[1] == 0)

    if valid(lm.get('hip')) and valid(lm.get('knee')) and valid(lm.get('ankle')):
        angles['knee'] = calculate_angle(lm['hip'], lm['knee'], lm['ankle'])
    else: angles['knee'] = 0

    if valid(lm.get('shoulder')) and valid(lm.get('hip')) and valid(lm.get('knee')):
        angles['hip'] = calculate_angle(lm['shoulder'], lm['hip'], lm['knee'])
    else: angles['hip'] = 0
    
    if valid(lm.get('shoulder')) and valid(lm.get('hip')):
        angles['back'] = calculate_angle_horizontal(lm['hip'], lm['shoulder'])
    else: angles['back'] = 0
    
    if valid(lm.get('elbow')) and valid(lm.get('shoulder')) and valid(lm.get('hip')):
        angles['arm_torso'] = calculate_angle(lm['elbow'], lm['shoulder'], lm['hip'])
    else: angles['arm_torso'] = 0

    if valid(lm.get('shoulder')) and valid(lm.get('elbow')) and valid(lm.get('wrist')):
        angles['elbow'] = calculate_angle(lm['shoulder'], lm['elbow'], lm['wrist'])
    else: angles['elbow'] = 0
    
    ear_pt = None
    if 'ear' in lm: ear_pt = lm['ear']
    elif 'side' in lm:
        ear_pt = lm.get(f"{lm['side']}_ear")
        
    if valid(ear_pt) and valid(lm.get('shoulder')) and valid(lm.get('hip')):
        angles['neck'] = calculate_angle(lm['hip'], lm['shoulder'], ear_pt)
    else: angles['neck'] = 0
    
    if valid(lm.get('elbow')) and valid(lm.get('wrist')):
        angles['wrist_tilt'] = calculate_angle_horizontal(lm['elbow'], lm['wrist'])
    else: angles['wrist_tilt'] = 0
    
    # Real Foot Angle (Heel-Toe vs Horizontal)
    # 0 deg = flat. + deg = toe up?
    if valid(lm.get('heel')) and valid(lm.get('toe')):
        angles['foot_angle'] = calculate_angle_horizontal(lm['heel'], lm['toe'])
    else: angles['foot_angle'] = 0

    return angles

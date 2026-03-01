import cv2
import time

def test_cameras():
    print("Testing cameras...")
    valid_cams = []
    
    # Test DSHOW first
    for i in range(5):
        print(f"\nTesting index {i} with CAP_DSHOW...")
        try:
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                print(f"  [{i} DSHOW] Opened successfully.")
                valid = False
                for j in range(20):
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        max_val = frame.max()
                        if max_val > 15:
                            valid = True
                            print(f"  [{i} DSHOW] -> WORKING CAMERA FOUND (max_val: {max_val} on frame {j})")
                            break
                    time.sleep(0.1)
                
                if valid:
                    valid_cams.append((i, cv2.CAP_DSHOW))
                else:
                    print(f"  [{i} DSHOW] -> Stream is black/empty")
                cap.release()
            else:
                print(f"  [{i} DSHOW] Failed to open.")
        except Exception as e:
            print(f"  [{i} DSHOW] Exception: {e}")

    # Test default backend
    for i in range(5):
        print(f"\nTesting index {i} with CAP_ANY (default)...")
        try:
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                print(f"  [{i} ANY] Opened successfully.")
                valid = False
                for j in range(20):
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        max_val = frame.max()
                        if max_val > 15:
                            valid = True
                            print(f"  [{i} ANY] -> WORKING CAMERA FOUND (max_val: {max_val} on frame {j})")
                            break
                    time.sleep(0.1)
                
                if valid:
                    valid_cams.append((i, cv2.CAP_ANY))
                else:
                    print(f"  [{i} ANY] -> Stream is black/empty")
                cap.release()
            else:
                print(f"  [{i} ANY] Failed to open.")
        except Exception as e:
             print(f"  [{i} ANY] Exception: {e}")
             
    print("\nSummary of working combinations:")
    for idx, backend in valid_cams:
        print(f"- Index {idx}, Backend {backend}")

if __name__ == '__main__':
    test_cameras()

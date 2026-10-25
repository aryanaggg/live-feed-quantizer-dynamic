# camera_feed.py
import ctypes
import cv2
import numpy as np

def _get_screen_size():
    try:
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


def start_camera(frame_callback):
    """
    Opens webcam and sends each frame to frame_callback(frame).
    Press 'q' to exit.
    """

    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("Error: Cannot open webcam")
        return

    screen_w, screen_h = _get_screen_size()

    cv2.namedWindow("Live Quantizer", cv2.WINDOW_NORMAL)
    cv2.setWindowProperty("Live Quantizer", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    fullscreen = True

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Camera stream ended.")
            break

        # Mirror for natural webcam effect
        frame = cv2.flip(frame, 1)

        # Send frame to the processing function
        processed_frame = frame_callback(frame)

        if processed_frame is None:
            processed_frame = frame

        # Preserve aspect ratio by scaling into a fullscreen black canvas.
        frame_h, frame_w = processed_frame.shape[:2]
        scale = min(screen_w / frame_w, screen_h / frame_h)
        target_w = int(frame_w * scale)
        target_h = int(frame_h * scale)

        resized = cv2.resize(processed_frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
        canvas = np.zeros((screen_h, screen_w, 3), dtype=np.uint8)
        y_offset = (screen_h - target_h) // 2
        x_offset = (screen_w - target_w) // 2
        canvas[y_offset:y_offset + target_h, x_offset:x_offset + target_w] = resized

        cv2.imshow("Live Quantizer", canvas)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('f'):
            fullscreen = not fullscreen
            mode = cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL
            cv2.setWindowProperty("Live Quantizer", cv2.WND_PROP_FULLSCREEN, mode)
        elif key == ord('F'):
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    start_camera(None)
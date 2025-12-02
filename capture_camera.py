# camera_feed.py
import cv2

def start_camera(frame_callback):
    """
    Opens webcam and sends each frame to frame_callback(frame).
    Press 'q' to exit.
    """

    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("Error: Cannot open webcam")
        return

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Camera stream ended.")
            break

        # Mirror for natural webcam effect
        frame = cv2.flip(frame, 1)

        # Send frame to the processing function
        processed_frame = frame_callback(frame)

        # Display the processed frame
        cv2.imshow("Live Quantizer", processed_frame)

        if cv2.waitKey(1) & 0xFF == ord('F'):
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    start_camera(None)
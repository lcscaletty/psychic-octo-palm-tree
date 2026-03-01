import os
import time
import pyautogui

# --- Configuration ---
# Use the Playlist URL or URI here
PLAYLIST_LINK = "https://open.spotify.com/playlist/2VdPwVMqEGb6dwQVRlC88L?si=4c576b795ee84863"

def main():
    uri = PLAYLIST_LINK
    # Convert web URL to URI for the desktop app
    if "open.spotify.com" in uri:
        print("Converting web URL to Spotify URI...")
        parts = uri.split("/")
        if "playlist" in parts:
            playlist_id = parts[parts.index("playlist") + 1].split("?")[0]
            uri = f"spotify:playlist:{playlist_id}"
            
    print(f"Kineticode: Opening Spotify -> {uri}")
    
    # Open the Spotify URI using the Windows default handler
    try:
        os.startfile(uri)
    except Exception as e:
        print(f"Error opening Spotify: {e}")
        return

    # Wait for Spotify to open and focus 
    print("Waiting for Spotify to load and focus...")
    time.sleep(6) # Increased delay for desktop app navigation
    
    # Trigger playback
    # When a playlist URI is opened, 'Enter' usually starts playing the first track
    for i in range(2):
        print(f"Attempt {i+1}: Start Playback...")
        pyautogui.press('enter')
        time.sleep(1)
        pyautogui.press('playpause') # Global play/pause as a backup
        time.sleep(2)
    
    print("✅ Spotify Automation Complete!")

if __name__ == "__main__":
    main()

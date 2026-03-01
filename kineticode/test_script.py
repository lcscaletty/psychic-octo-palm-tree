import os
import sys
from datetime import datetime

def main():
    print("--- Kineticode Custom Script ---")
    print(f"Executed at: {datetime.now()}")
    print(f"Current Working Directory: {os.getcwd()}")
    
    # Create a small proof-of-work file
    try:
        with open("kineticode_test_output.txt", "a") as f:
            f.write(f"Script executed successfully at {datetime.now()}\n")
        print("Successfully wrote to kineticode_test_output.txt")
    except Exception as e:
        print(f"Error writing to file: {e}")

if __name__ == "__main__":
    main()

import subprocess
import time

p = subprocess.Popen(
    ["python", "kineticode/unified_engine.py", "--extension", "--hands", "--copy_paste"], 
    stdin=subprocess.PIPE, 
    stdout=subprocess.PIPE, 
    stderr=subprocess.PIPE, 
    text=True
)

print("Started Engine")
time.sleep(2)
p.stdin.write('{"event": "selection_changed", "hasSelection": true}\n')
p.stdin.flush()
print("Sent selection_changed = true")

print("Reading output:")
for _ in range(50): # read up to 50 lines
    line = p.stdout.readline()
    if not line: break
    print("STDOUT:", line.strip())

print("Reading stderr:")
stderr = p.stderr.read()
if stderr:
    print("STDERR:", stderr)

p.terminate()

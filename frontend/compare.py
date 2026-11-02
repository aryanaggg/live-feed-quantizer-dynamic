with open(r'F:\code\projects\live-feed-quantizer\frontend\index.html') as f:
    curr = f.read()
with open(r'F:\code\projects\live-feed-quantizer\frontend\index.html.bak') as f:
    bak = f.read()
# Find differences
curr_lines = curr.split('\n')
bak_lines = bak.split('\n')
print(f'Current: {len(curr_lines)} lines, Bak: {len(bak_lines)} lines')
# Find lines that differ
for i, (c, b) in enumerate(zip(curr_lines, bak_lines)):
    if c != b:
        print(f'Line {i+1} differs:')
        print(f'  CURR: {c[:100]}')
        print(f'  BAK:  {b[:100]}')
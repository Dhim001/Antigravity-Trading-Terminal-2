import re

path = 'frontend/src/components/ResizableDock.jsx'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# We need to clean up the multiple bad injections of `renderTabContent`.
# Look at the file carefully.
# The bad injections occurred wherever `  return (` was matched in the file.
# In `ResizableDock.jsx`, `return (` occurs in a few places.

# Let's read the file line by line and fix it manually because regex might be too dangerous.
lines = content.split('\n')
new_lines = []
skip = False
for i, line in enumerate(lines):
    if line.strip() == "const renderTabContent = (tabId, Component) => {":
        # Check if the next few lines are the injected block
        if "if (isDetached(tabId)) {" in lines[i+1]:
            skip = True
            continue
    
    if skip:
        # We are skipping the block. It ends with:
        # `    return <Component hideToolbar={false} />;`
        # `  };`
        if line.strip() == "};" and lines[i-1].strip() == "return <Component hideToolbar={false} />;":
            skip = False
            continue
        continue
        
    new_lines.append(line)

# Now we need to fix the stray `return () => {` that was separated from the `useEffect` cleanup.
# Actually, the original file had:
# window.addEventListener('dock-tab', onDockTab);
# window.addEventListener('dock-group', onDockGroup);
# return () => { window.removeEventListener('dock-tab', onDockTab); window.removeEventListener('dock-group', onDockGroup); };

# Wait, the injection also broke the `return () => {` blocks!
# Let's restore the whole component from a known state or just fix it.

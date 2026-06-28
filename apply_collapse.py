import sys

def modify():
    path = 'frontend/src/components/dock/PositionsPanel.jsx'
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    if 'import CollapsibleCard' not in content:
        content = content.replace("import { Briefcase } from 'lucide-react';", "import { Briefcase } from 'lucide-react';\nimport CollapsibleCard from './CollapsibleCard';")
        content = content.replace("    <div className=\"dock-panel-tab dock-panel-tab--positions h-full flex flex-col\">", "    <div className=\"dock-panel-tab dock-panel-tab--positions h-full flex flex-col p-2 space-y-2 overflow-y-auto\">\n      <CollapsibleCard title=\"Open Positions\" icon={Briefcase} badge={entries.length} className=\"flex-shrink-0\" contentClassName=\"max-h-[600px] overflow-y-auto\">")
        content = content.replace("    </div>", "      </CollapsibleCard>\n    </div>")
        content = content.replace("<header className=\"dock-panel-tab__header\">", "<header className=\"dock-panel-tab__header hidden\">")

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == '__main__':
    modify()

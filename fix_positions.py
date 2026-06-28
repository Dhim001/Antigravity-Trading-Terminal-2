import sys

def modify():
    path = 'frontend/src/components/dock/PositionsPanel.jsx'
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    content = content.replace('            </CollapsibleCard>\n    </div>', '            </div>')
    content = content.replace('            </CollapsibleCard>\n          </div>', '          </div>')
    content = content.replace('          </CollapsibleCard>\n        </div>', '        </div>')
    content = content.replace('        </CollapsibleCard>\n      </div>', '      </div>')
    content = content.replace('      </CollapsibleCard>\n    </div>', '    </div>')
    content = content.replace('</CollapsibleCard>', '')

    content = content.replace('<header className="dock-panel-tab__header hidden">', '<header className="dock-panel-tab__header">')
    content = content.replace('<CollapsibleCard title="Open Positions" icon={Briefcase} badge={entries.length} className="flex-shrink-0" contentClassName="max-h-[600px] overflow-y-auto">', '')
    content = content.replace("import CollapsibleCard from './CollapsibleCard';", "")
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == '__main__':
    modify()

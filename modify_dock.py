import sys

def modify():
    path = 'frontend/src/components/ResizableDock.jsx'
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    start_idx = -1
    end_idx = -1
    
    for i, line in enumerate(lines):
        if line.startswith('// ── Tiny formatters'):
            start_idx = i
        if line.startswith('// ── Main ResizableDock'):
            end_idx = i
            break
            
    if start_idx == -1 or end_idx == -1:
        print("Could not find markers")
        return
        
    imports = """
import PositionsTab from './dock/PositionsPanel';
import OrdersTab from './dock/OrdersPanel';
import BalancesTab from './dock/BalancesPanel';
import { AlgoTab } from './dock/AlgoPanel';
import GlobalDeployDialog from './dock/GlobalDeployDialog';
"""

    new_lines = lines[:start_idx] + [imports] + lines[end_idx:]
    
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
        
if __name__ == '__main__':
    modify()

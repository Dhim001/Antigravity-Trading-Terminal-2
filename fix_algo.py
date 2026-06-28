import sys

def modify():
    path = 'frontend/src/components/dock/AlgoPanel.jsx'
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    import_str = "import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';\n"
    if 'DialogContent' not in content:
        content = content.replace("import { Badge } from '@/components/ui/badge';", "import { Badge } from '@/components/ui/badge';\n" + import_str)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == '__main__':
    modify()

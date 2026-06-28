import json
import uuid
import time
from starlette.requests import Request
from starlette.responses import JSONResponse
from app.db.connection import get_connection

async def get_workspaces_handler(request: Request) -> JSONResponse:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, name, state_json, created_at, updated_at FROM workspaces ORDER BY updated_at DESC")
        rows = cursor.fetchall()
        workspaces = []
        for row in rows:
            try:
                state = json.loads(row[2])
            except:
                state = {}
            workspaces.append({
                "id": row[0],
                "name": row[1],
                "state": state,
                "created_at": row[3],
                "updated_at": row[4]
            })
        return JSONResponse({"ok": True, "workspaces": workspaces})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    finally:
        cursor.close()
        conn.close()

async def save_workspace_handler(request: Request) -> JSONResponse:
    try:
        body = await request.json()
        name = body.get("name")
        state = body.get("state")
        workspace_id = body.get("id", str(uuid.uuid4()))
        
        if not name or not state:
            return JSONResponse({"ok": False, "error": "Name and state are required"}, status_code=400)
            
        state_json = json.dumps(state)
        
        conn = get_connection()
        cursor = conn.cursor()
        
        # Insert or update
        cursor.execute("SELECT id FROM workspaces WHERE id = ?", (workspace_id,))
        if cursor.fetchone():
            cursor.execute(
                "UPDATE workspaces SET name = ?, state_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (name, state_json, workspace_id)
            )
        else:
            cursor.execute(
                "INSERT INTO workspaces (id, name, state_json) VALUES (?, ?, ?)",
                (workspace_id, name, state_json)
            )
            
        conn.commit()
        return JSONResponse({"ok": True, "workspace_id": workspace_id})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

async def delete_workspace_handler(request: Request) -> JSONResponse:
    workspace_id = request.path_params.get("workspace_id")
    if not workspace_id:
        return JSONResponse({"ok": False, "error": "Workspace ID required"}, status_code=400)
        
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
        conn.commit()
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

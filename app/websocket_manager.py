from typing import Dict, List
from fastapi import WebSocket, WebSocketDisconnect
import logging
import json

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        # Store active connections: key (e.g., transcription_id or custom_id) -> List[WebSocket]
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        if client_id not in self.active_connections:
            self.active_connections[client_id] = []
        self.active_connections[client_id].append(websocket)
        logger.info(f"Client {client_id} connected via WebSocket")

    def disconnect(self, websocket: WebSocket, client_id: str):
        if client_id in self.active_connections:
            if websocket in self.active_connections[client_id]:
                self.active_connections[client_id].remove(websocket)
            if not self.active_connections[client_id]:
                del self.active_connections[client_id]
        logger.info(f"Client {client_id} disconnected")

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)

    async def broadcast(self, message: dict, client_id: str):
        """
        Broadcast a message to all connections associated with a specific client_id (e.g., transcription_id)
        """
        if client_id in self.active_connections:
            # Create a copy of the list to iterate over safely
            connections = self.active_connections[client_id][:]
            for connection in connections:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error(f"Error broadcasting to {client_id}: {e}")
                    # If sending fails, we might want to remove the connection, 
                    # but usually WebSocketDisconnect handles that.

manager = ConnectionManager()

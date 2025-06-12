import express from 'express';
import { createServer } from 'http';
import { Server } from 'socket.io';
import path from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs';

const TOPIC_NAME = process.env.TOPIC_NAME || "notifications";

// Get directory name in ES modules
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Create Express app and HTTP server
const app = express();
const server = createServer(app);

// Create Socket.IO server with verbose debugging
const io = new Server(server, {
  cors: {
    origin: "*",
    methods: ["GET", "POST"]
  },
  // Enable Socket.IO debugging
  debug: true
});

// Check if dist directory exists (production mode)
const isDist = fs.existsSync(path.join(__dirname, 'dist'));

// In production, serve static files from 'dist'
if (isDist) {
  console.log('Running in production mode, serving from dist directory');
  app.use(express.static(path.join(__dirname, 'dist')));
}

// Parse JSON request body with more logging
app.use(express.json({ 
  limit: '10mb',
  type: ['application/json', 'application/cloudevents+json'] 
}));

// Socket.IO connection handler with more logging
io.on('connection', (socket) => {
  const clientId = socket.id;
  console.log(`[${new Date().toISOString()}] Client connected:`, clientId);
  
  // Send a test message to confirm connection
  setTimeout(() => {
    console.log(`[${new Date().toISOString()}] Sending test message to client:`, clientId);
    socket.emit('message', { 
      message: JSON.stringify({
        order_id: "test_connection", 
        message: "Connection test successful"
      })
    });
  }, 1000);
  
  socket.on('disconnect', () => {
    console.log(`[${new Date().toISOString()}] Client disconnected:`, clientId);
  });
});

// Topic notification handler
app.post('/' + TOPIC_NAME, (req, res) => {
  try {
    const timestamp = new Date().toISOString();
    console.log(`[${timestamp}] Received event:`, JSON.stringify(req.body));
    
    // Extract the message data
    const event = req.body;
    const message = event.data || '';
    
    console.log(`[${timestamp}] Processing message:`, message);
    
    // Emit the message to all connected clients
    io.emit('message', { message });
    console.log(`[${timestamp}] Emitted message to all clients`);
    
    return res.status(200).send();
  } catch (error) {
    console.error('Error processing message:', error);
    res.status(500).json({
      success: false,
      error: 'Failed to process message'
    });
  }
});

// Health check endpoint
app.get("/healthz", (req, res) => {
  console.log("Health check requested");
  return res.status(200).send(`Hello from Node.js server`);
});

// In production, handle all routes
if (isDist) {
  app.get('*', (req, res) => {
    res.sendFile(path.join(__dirname, 'dist', 'index.html'));
  });
}

// Start the server
const PORT = process.env.PORT || 8080;
server.listen(PORT, () => {
  console.log(`[${new Date().toISOString()}] Server running on port ${PORT}`);
  console.log(`- API endpoint: http://localhost:${PORT}/${TOPIC_NAME}`);
  console.log(`- Socket.IO: ws://localhost:${PORT}`);
  if (isDist) {
    console.log(`- Web UI: http://localhost:${PORT}`);
  } else {
    console.log(`- Web UI: Use the Vite dev server`);
  }
});
// src/components/MessageReceiver.tsx
import React, { useEffect, useContext, useRef } from 'react';
import { io } from 'socket.io-client';
import { MessageContext } from '@/context/MessageContext';
import { useToast } from '@/hooks/use-toast';

// Global connection status for UI
window._connectionStatus = 'connecting';

const MessageReceiver = () => {
  const { addMessage } = useContext(MessageContext);
  const { toast } = useToast();
  const socketRef = useRef(null);
  const isConnectedRef = useRef(false);

  useEffect(() => {
    const wsURL = window.location.protocol + "//" + document.location.host;
    console.log("Connecting to Socket.IO at:", wsURL);

    // Create socket instance with autoConnect: false
    const socket = io(wsURL, { autoConnect: false });
    socketRef.current = socket;

    // Register listeners BEFORE calling connect
    socket.on('message', (data) => {
      console.log("Received message:", data);

      try {
        let orderData;
        if (typeof data.message === 'string') {
          try {
            orderData = JSON.parse(data.message);
          } catch (e) {
            orderData = {
              order_id: 'unknown',
              message: data.message || 'Error parsing message',
            };
          }
        } else {
          orderData = { order_id: 'unknown', message: 'Notification received' };
        }

        const messageObj = {
          order_id: orderData.order_id || 'unknown',
          message: orderData.message || 'Order notification received',
          timestamp: new Date().toLocaleTimeString(),
        };

        console.log("Processing message:", messageObj);
        addMessage(messageObj);
      } catch (error) {
        console.error("Error processing message:", error);
      }
    });

    socket.on('connect', () => {
      console.log("Connected to " + wsURL);
      window._connectionStatus = 'open';
      isConnectedRef.current = true;
    });

    socket.on('disconnect', (reason) => {
      console.log("Connection closed:", reason);
      window._connectionStatus = 'closed';
      isConnectedRef.current = false;

      if (reason !== 'io client disconnect') {
        toast({
          title: 'Connection Closed',
          description: `WebSocket connection closed: ${reason}`,
          variant: 'destructive',
        });
      }
    });

    // Connect AFTER listeners are registered
    socket.connect();

    // Cleanup on unmount
    return () => {
      if (socketRef.current) {
        socketRef.current.disconnect();
        socketRef.current = null;
      }
      isConnectedRef.current = false;
    };
  }, [addMessage, toast]);

  return null;
};

export default MessageReceiver;

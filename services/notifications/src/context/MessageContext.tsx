// src/context/MessageContext.tsx
import React, { createContext, useState, useRef, ReactNode, useCallback } from 'react';

// Maximum number of messages to keep in memory
const MAX_MESSAGES = 1000;

// Message type
export interface Message {
  order_id: string;
  message: string;
  timestamp: string;
}

// Context type
interface MessageContextType {
  messages: Message[];
  addMessage: (message: Message) => void;
  clearMessages: () => void;
}

// Create the context with default values
export const MessageContext = createContext<MessageContextType>({
  messages: [],
  addMessage: () => {},
  clearMessages: () => {}
});

// Helper to limit the size of processed message set
const trimProcessedMessages = (processedSet: Set<string>) => {
  const idsArray = Array.from(processedSet);
  processedSet.clear();
  idsArray.slice(idsArray.length - MAX_MESSAGES / 2).forEach(id =>
    processedSet.add(id)
  );
};

// Context provider component
export const MessageProvider = ({ children }: { children: ReactNode }) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const processedMessages = useRef(new Set<string>());

  // Add a new message to the list
  const addMessage = useCallback((message: Message) => {
    // More unique identifier to reduce risk of false duplicates
    const messageId = `${message.order_id}-${message.message}-${message.timestamp}`;

    if (processedMessages.current.has(messageId)) {
      if (process.env.NODE_ENV === 'development') {
        console.log("Skipping duplicate message:", messageId);
      }
      return;
    }

    processedMessages.current.add(messageId);

    setMessages(prevMessages => {
      const newMessages = [...prevMessages, message];
      if (newMessages.length > MAX_MESSAGES) {
        return newMessages.slice(newMessages.length - MAX_MESSAGES);
      }
      return newMessages;
    });

    if (processedMessages.current.size > MAX_MESSAGES) {
      trimProcessedMessages(processedMessages.current);
    }
  }, []);

  // Clear all messages
  const clearMessages = useCallback(() => {
    setMessages([]);
    processedMessages.current.clear();
  }, []);

  return (
    <MessageContext.Provider value={{ messages, addMessage, clearMessages }}>
      {children}
    </MessageContext.Provider>
  );
};

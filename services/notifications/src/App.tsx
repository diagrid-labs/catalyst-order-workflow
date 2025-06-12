// src/App.tsx
import React, { useState, useEffect } from 'react';
import Header from './components/Header';
import MessageLog from './components/MessageLog';
import MessageReceiver from './components/MessageReceiver';
import { Card } from '@/components/ui/card';
import { MessageProvider } from './context/MessageContext';

// Declare the global connection status accessor
declare global {
  interface Window {
    _connectionStatus: 'open' | 'closed' | 'connecting';
  }
}

const App = () => {
  const [connectionStatus, setConnectionStatus] = useState<'open' | 'closed' | 'connecting'>('connecting');

  // Check connection status periodically
  useEffect(() => {
    // Update status every 2 seconds
    const intervalId = setInterval(() => {
      setConnectionStatus(window._connectionStatus);
    }, 2000);
    
    return () => clearInterval(intervalId);
  }, []);

  return (
    <MessageProvider>
      <div className="min-h-screen flex flex-col">
        {/* Socket connection handler */}
        <MessageReceiver />
        
        <Header connectionStatus={connectionStatus} />
        
        <main className="flex-1 container mx-auto my-4 flex">
          <Card className="flex-1 flex flex-col h-[calc(100vh-8rem)] shadow-lg overflow-hidden">
            <MessageLog />
          </Card>
        </main>
      </div>
    </MessageProvider>
  );
};

export default App;
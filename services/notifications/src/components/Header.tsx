
import React from 'react';
import { Badge } from '@/components/ui/badge';
import { Activity, UploadCloud } from 'lucide-react';

interface HeaderProps {
  connectionStatus: 'open' | 'closed' | 'connecting';
  messageCount: number;
}

const Header = ({ connectionStatus, messageCount }: HeaderProps) => {
  return (
    <header className="bg-card p-4 border-b border-border flex items-center justify-between">
      <div className="flex items-center">
        {/* <img 
          src="/lovable-uploads/1ea7dc77-9ddb-4785-b63f-9a452ce2d02b.png" 
          alt="Dapr Logo" 
          className="h-10 mr-4"
        /> */}
        <div>
          <h1 className="text-xl font-bold">Order Workflow</h1>
        </div>
      </div>
      <div className="flex items-center gap-4">
        <Badge variant={connectionStatus === 'open' ? 'default' : 'destructive'} className="flex gap-1 items-center">
          <UploadCloud className="h-3 w-3" />
          <span>connection: {connectionStatus}</span>
        </Badge>
      </div>
    </header>
  );
};

export default Header;

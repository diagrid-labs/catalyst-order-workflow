// src/components/MessageLog.tsx
import React, { useRef, useEffect, useContext, useMemo } from 'react';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { MessageContext } from '@/context/MessageContext';

// Color palette for order IDs - defined once
const COLOR_PALETTE = [
  '#8B5CF6', '#D946EF', '#F97316', '#0EA5E9', '#10B981', 
  '#F59E0B', '#EC4899', '#6366F1', '#14B8A6', '#EF4444'
];

// Simple object to store color mappings
const orderColorMap: Record<string, string> = {};

const MessageLog = () => {
  const { messages } = useContext(MessageContext);
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  
  // Get color for an order ID
  const getColorForOrderId = (orderId: string): string => {
    if (!orderColorMap[orderId]) {
      // Assign a color based on the number of unique order IDs
      const colorIndex = Object.keys(orderColorMap).length % COLOR_PALETTE.length;
      orderColorMap[orderId] = COLOR_PALETTE[colorIndex];
    }
    return orderColorMap[orderId];
  };

  // Auto scroll to bottom when new messages arrive
  useEffect(() => {
    if (scrollAreaRef.current) {
      // Get the scroll container from the ScrollArea component
      const scrollContainer = scrollAreaRef.current.querySelector('[data-radix-scroll-area-viewport]');
      if (scrollContainer) {
        scrollContainer.scrollTop = scrollContainer.scrollHeight;
      }
    }
  }, [messages]);

  // Group messages by order ID - only recalculate when messages change
  const groupedByOrderId = useMemo(() => {
    const groups: Record<string, any[]> = {};
    
    messages.forEach(message => {
      const orderId = message.order_id || 'unknown';
      if (!groups[orderId]) {
        groups[orderId] = [];
      }
      groups[orderId].push(message);
    });
    
    return groups;
  }, [messages]);

  // Get order IDs sorted by first message timestamp
  const sortedOrderIds = useMemo(() => {
    return Object.keys(groupedByOrderId).sort((a, b) => {
      const aTimestamp = groupedByOrderId[a][0].timestamp || '';
      const bTimestamp = groupedByOrderId[b][0].timestamp || '';
      return aTimestamp.localeCompare(bTimestamp);
    });
  }, [groupedByOrderId]);

  return (
    <div className="flex-1 h-full flex flex-col">
      <ScrollArea className="flex-1 pr-4" ref={scrollAreaRef}>
        <div className="p-4" id="events">
          {messages.length > 0 ? (
            <div className="mb-4">
              {sortedOrderIds.map((orderId, groupIndex) => (
                <div key={orderId}>
                  {groupIndex > 0 && <Separator className="my-3" />}
                  
                  {groupedByOrderId[orderId].map((message, index) => (
                    <div key={`${orderId}-${index}`} className="item mb-3">
                      <div className="item-text">
                        <i>{message.timestamp}</i> | <b style={{ color: getColorForOrderId(orderId) }}>{orderId}</b> | <i>{message.message}</i>
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          ) : (
            <div className="flex items-center justify-center h-40">
              <p className="text-muted-foreground">No messages yet</p>
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
};

export default MessageLog;
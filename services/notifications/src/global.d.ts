
interface Window {
  _getConnectionStatus: () => 'open' | 'closed' | 'connecting';
  _healthCheck: () => { status: string; timestamp: string; };
  _postToEndpoint: (topicName: string, data: any) => Promise<boolean>;
}

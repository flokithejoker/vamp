import { Navigate, Route, Routes } from 'react-router-dom';
import { AppLayout } from './layout/AppLayout';
import { AgentSettingsPage } from './pages/AgentSettingsPage';
import { FeedbackPage } from './pages/FeedbackPage';
import { HomePage } from './pages/HomePage';
import { MonitoringConversationPage } from './pages/MonitoringConversationPage';
import { MonitoringPage } from './pages/MonitoringPage';
import { SmartInsightsPage } from './pages/SmartInsightsPage';
import { StatisticsPage } from './pages/StatisticsPage';

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/agent-settings" element={<AgentSettingsPage />} />
        <Route path="/monitoring" element={<MonitoringPage />} />
        <Route path="/monitoring/:conversationId" element={<MonitoringConversationPage />} />
        <Route path="/statistics" element={<StatisticsPage />} />
        <Route path="/smart-insights" element={<SmartInsightsPage />} />
        <Route path="/feedback" element={<FeedbackPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

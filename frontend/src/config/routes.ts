export type AppRoute = {
  id: string;
  label: string;
  path: string;
  homeSummary: string;
  inHomeGrid: boolean;
  icon: 'home' | 'settings' | 'monitoring' | 'statistics' | 'feedback' | 'smartInsights';
};

export const appRoutes: AppRoute[] = [
  {
    id: 'home',
    label: 'Home',
    path: '/',
    homeSummary: 'Launchpad for all Viktoria control center features.',
    inHomeGrid: false,
    icon: 'home',
  },
  {
    id: 'monitoring',
    label: 'Conversations',
    path: '/monitoring',
    homeSummary: 'Review live and recent conversations, transcripts, and outcomes.',
    inHomeGrid: true,
    icon: 'monitoring',
  },
  {
    id: 'statistics',
    label: 'Statistics',
    path: '/statistics',
    homeSummary: 'Track call volume, duration, and technical usage.',
    inHomeGrid: true,
    icon: 'statistics',
  },
  {
    id: 'smartInsights',
    label: 'Smart Insights',
    path: '/smart-insights',
    homeSummary: 'LLM-generated shift briefings, hotspots, and prioritized action queues.',
    inHomeGrid: true,
    icon: 'smartInsights',
  },
  {
    id: 'feedback',
    label: 'Feedback',
    path: '/feedback',
    homeSummary: 'Analyze customer ratings and call comments.',
    inHomeGrid: true,
    icon: 'feedback',
  },
];

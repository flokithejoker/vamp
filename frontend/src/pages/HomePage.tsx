import { HomeCard } from '../components/HomeCard';
import { appRoutes } from '../config/routes';

export function HomePage() {
  const homeRoutes = appRoutes.filter((route) => route.inHomeGrid);

  return (
    <section className="page-shell home-page">
      <div className="page-surface page-heading home-hero">
        <h2>Welcome to Viktoria.</h2>
      </div>

      <div className="home-grid">
        {homeRoutes.map((route) => (
          <HomeCard
            key={route.id}
            label={route.label}
            summary={route.homeSummary}
            path={route.path}
            icon={route.icon}
          />
        ))}
      </div>
    </section>
  );
}

import { Link } from 'react-router-dom';
import type { AppRoute } from '../config/routes';
import { ArrowUpRightIcon, RouteIcon } from './RouteIcon';

type HomeCardProps = {
  label: string;
  summary: string;
  path: string;
  icon: AppRoute['icon'];
};

export function HomeCard({ label, summary, path, icon }: HomeCardProps) {
  return (
    <Link to={path} className="home-card">
      <span className="home-card-icon" aria-hidden="true">
        <RouteIcon name={icon} className="icon-20" />
      </span>

      <div className="home-card-content">
        <h3>{label}</h3>
        <p>{summary}</p>
      </div>

      <span className="home-card-arrow" aria-hidden="true">
        <ArrowUpRightIcon className="icon-16" />
      </span>
    </Link>
  );
}

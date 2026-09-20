import { analysis } from './analysis';
import { enCommon } from './common';
import { enChart } from './chart';
import { enNav } from './nav';
import { enDash } from './dash';
import { enAdmin } from './admin';
import { enDocs } from './docs';

export const enUS = {
  ...enCommon,
  chart: enChart,
  nav: enNav,
  dash: enDash,
  admin: enAdmin,
  analysis,
  docs: enDocs,
};

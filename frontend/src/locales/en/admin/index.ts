import { enAdminShell } from './shell';
import { enAdminLogin } from './login';
import { enAdminOverview } from './overview';
import { enAdminPolicySnapshot } from './policySnapshot';
import { enAdminPromptStudio } from './promptStudio';
import { enAdminAdminSys } from './adminSys';
import { enAdminGateway } from './gateway';
import { enAdminAbout } from './about';
import { enAdminBackup } from './backup';
import { enAdminPlugins } from './plugins';
import { enAdminAudit } from './audit';
import { enAdminAgents } from './agents';
import { enAdminCouncil } from './council';
import { enAdminInterceptors } from './interceptors';
import { enAdminLlm } from './llm';
import { enAdminLegacy } from './legacy';
import { enAdminSecurity } from './security';
import { enAdminDecisions } from './decisions';
import { enAdminEvolution } from './evolution';
import { enAdminNotify } from './notify';
import { enAdminRisk } from './risk';

export const enAdmin = {
  shell: enAdminShell,
  login: enAdminLogin,
  overview: enAdminOverview,
  policySnapshot: enAdminPolicySnapshot,
  promptStudio: enAdminPromptStudio,
  adminsys: enAdminAdminSys,
  gateway: enAdminGateway,
  about: enAdminAbout,
  backup: enAdminBackup,
  plugins: enAdminPlugins,
  audit: enAdminAudit,
  agents: enAdminAgents,
  council: enAdminCouncil,
  interceptors: enAdminInterceptors,
  llm: enAdminLlm,
  legacy: enAdminLegacy,
  security: enAdminSecurity,
  decisions: enAdminDecisions,
  evolution: enAdminEvolution,
  notify: enAdminNotify,
  risk: enAdminRisk,
};

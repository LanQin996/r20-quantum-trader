import { zhAdminShell } from './shell';
import { zhAdminLogin } from './login';
import { zhAdminOverview } from './overview';
import { zhAdminPolicySnapshot } from './policySnapshot';
import { zhAdminPromptStudio } from './promptStudio';
import { zhAdminAdminSys } from './adminSys';
import { zhAdminGateway } from './gateway';
import { zhAdminAbout } from './about';
import { zhAdminBackup } from './backup';
import { zhAdminPlugins } from './plugins';
import { zhAdminAudit } from './audit';
import { zhAdminAgents } from './agents';
import { zhAdminCouncil } from './council';
import { zhAdminInterceptors } from './interceptors';
import { zhAdminLlm } from './llm';
import { zhAdminLegacy } from './legacy';
import { zhAdminSecurity } from './security';
import { zhAdminDecisions } from './decisions';
import { zhAdminEvolution } from './evolution';
import { zhAdminNotify } from './notify';
import { zhAdminRisk } from './risk';

/** 控制台文案聚合：各页键位随页面重建逐页追加 */
export const zhAdmin = {
  shell: zhAdminShell,
  login: zhAdminLogin,
  overview: zhAdminOverview,
  policySnapshot: zhAdminPolicySnapshot,
  promptStudio: zhAdminPromptStudio,
  adminsys: zhAdminAdminSys,
  gateway: zhAdminGateway,
  about: zhAdminAbout,
  backup: zhAdminBackup,
  plugins: zhAdminPlugins,
  audit: zhAdminAudit,
  agents: zhAdminAgents,
  council: zhAdminCouncil,
  interceptors: zhAdminInterceptors,
  llm: zhAdminLlm,
  legacy: zhAdminLegacy,
  security: zhAdminSecurity,
  decisions: zhAdminDecisions,
  evolution: zhAdminEvolution,
  notify: zhAdminNotify,
  risk: zhAdminRisk,
};

import { SiteNav } from './components/SiteNav'
import { SiteFooter } from './components/SiteFooter'
import { HeroSection } from './sections/HeroSection'
import { LoopSection } from './sections/LoopSection'
import { ToolsSection } from './sections/ToolsSection'
import { AgentAnatomySection } from './sections/AgentAnatomySection'
import { AgentBuilderSection } from './sections/AgentBuilderSection'
import { GallerySection } from './sections/GallerySection'
import { DeploymentsSection } from './sections/DeploymentsSection'
import { RegistrySection } from './sections/RegistrySection'
import { CtaSection } from './sections/CtaSection'

export default function App() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <SiteNav />
      <main id="main">
        <HeroSection />
        <LoopSection />
        <ToolsSection />
        <AgentAnatomySection />
        <AgentBuilderSection />
        <GallerySection />
        <DeploymentsSection />
        <RegistrySection />
        <CtaSection />
      </main>
      <SiteFooter />
    </>
  )
}

#Requires -Version 7.0
#Requires -Modules Pester

BeforeAll {
    $windowsRoot = Split-Path -Parent $PSScriptRoot
    . (Join-Path $windowsRoot 'TemplateBuilder.ps1')
    . (Join-Path $windowsRoot 'EpicVM.Agent.ps1') -NoStart -ConfigPath (Join-Path $windowsRoot 'config.example.json')
}

Describe 'EpicVM template builder' {
    It 'does not execute during dot-sourcing and exposes source-only safety gates' {
        (Get-Command Invoke-EpicVMTemplateBuild).CommandType | Should -Be 'Function'
        (Get-Content (Join-Path $windowsRoot 'TemplateBuilder.ps1') -Raw) | Should -Match 'Private'
        (Get-Content (Join-Path $windowsRoot 'TemplateBuilder.ps1') -Raw) | Should -Match '/generalize /oobe /shutdown /mode:vm'
        (Get-Content (Join-Path $windowsRoot 'TemplateBuilder.ps1') -Raw) | Should -Match 'Export-VM'
    }

    It 'rejects any source name other than the verified testre gate' {
        { Invoke-EpicVMTemplateBuild -SourceName 'other-vm' } | Should -Throw '*testre*'
    }

    It 'does not embed a bootstrap secret in the manifest contract' {
        $text = Get-Content (Join-Path $windowsRoot 'TemplateBuilder.ps1') -Raw
        $text | Should -Not -Match 'Password\s*=\s*["'']'
        $text | Should -Match 'machine-dpapi-encrypted-system-admin'
        $text | Should -Match 'immutable=\$true'
    }
}

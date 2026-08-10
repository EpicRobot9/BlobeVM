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

    It 'creates the protected bootstrap parent on the isolated guest copy' {
        (Get-EpicVMTemplateGuestSanitizer).ToString() | Should -Match 'bootstrapParent'
        (Get-EpicVMTemplateGuestSanitizer).ToString() | Should -Match 'New-Item -ItemType Directory'
    }

    It 'restarts the source immediately after the independent builder copy' {
        $builder = Get-Content -LiteralPath (Join-Path $windowsRoot 'TemplateBuilder.ps1') -Raw
        $copyIndex = $builder.IndexOf('Destination $builderDisk')
        $restartIndex = $builder.IndexOf('Name=$SourceName; ErrorAction=''Stop''', $copyIndex)
        $switchIndex = $builder.IndexOf("Get-VMSwitch", $copyIndex)
        $copyIndex | Should -BeGreaterThan -1
        $restartIndex | Should -BeGreaterThan $copyIndex
        $restartIndex | Should -BeLessThan $switchIndex
    }

    It 'accepts only the expected Sysprep shutdown transport signature' {
        $builder = Get-Content -LiteralPath (Join-Path $windowsRoot 'TemplateBuilder.ps1') -Raw
        $builder | Should -Match 'remote session might have ended'
        $builder | Should -Match 'builder_shutdown_timeout'
    }
}

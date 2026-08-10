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

    It 'rotates the machine-protected bootstrap blob atomically on retry' {
        $builder = Get-Content -LiteralPath (Join-Path $windowsRoot 'TemplateBuilder.ps1') -Raw
        $builder | Should -Match "\.bootstrap-"
        $builder | Should -Match 'Move-Item -LiteralPath \$temporaryPath -Destination \$Path -Force'
        $builder | Should -Not -Match 'WriteAllBytes\(\$Path,\$protected\)'
        $builder | Should -Match 'takeown\.exe'
        $builder | Should -Match "Administrators','FullControl'"
    }

    It 'flattens the active exported disk chain before restarting the source' {
        $builder = Get-Content -LiteralPath (Join-Path $windowsRoot 'TemplateBuilder.ps1') -Raw
        $convertIndex = $builder.IndexOf("Name 'Convert-VHD'")
        $restartIndex = $builder.IndexOf('Name=$SourceName; ErrorAction=''Stop''', $convertIndex)
        $switchIndex = $builder.IndexOf("Get-VMSwitch", $convertIndex)
        $convertIndex | Should -BeGreaterThan -1
        $restartIndex | Should -BeGreaterThan $convertIndex
        $restartIndex | Should -BeLessThan $switchIndex
        $builder | Should -Match 'SourceLeafName'
        $builder | Should -Match 'source_flatten_failed'
    }

    It 'disables builder checkpoints and requires the exact Off state' {
        $builder = Get-Content -LiteralPath (Join-Path $windowsRoot 'TemplateBuilder.ps1') -Raw
        $builder | Should -Match 'AutomaticCheckpointsEnabled=\$false'
        $builder | Should -Match "CheckpointType='Disabled'"
        $builder | Should -Match "builderState -ieq 'Off'"
    }

    It 'accepts only the expected Sysprep shutdown transport signature' {
        $builder = Get-Content -LiteralPath (Join-Path $windowsRoot 'TemplateBuilder.ps1') -Raw
        $builder | Should -Match 'remote session might have ended'
        $builder | Should -Match 'builder_shutdown_timeout'
    }

    It 'bounds guest sanitation and surfaces Sysprep failures immediately' {
        $builder = Get-Content -LiteralPath (Join-Path $windowsRoot 'TemplateBuilder.ps1') -Raw
        $builder | Should -Match 'Invoke-Command -VMName \$VmName.*-AsJob'
        $builder | Should -Match 'Wait-Job -Job \$guestJob -Timeout 1200'
        $builder | Should -Match 'guest_sanitation_timeout'
        $builder | Should -Match 'sysprep_failed exit='
        $builder | Should -Match 'setuperr\.log'
        $builder | Should -Match 'Remove-AppxPackage -Package \$_.PackageFullName -User \$userSid'
        $builder | Should -Not -Match 'Get-WinEvent -ListLog \*'
    }
}
